from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core import ai
from customers.models import Customer
from notifications.models import Notification
from ytchannels.models import Channel

from .anomalies import check_drop, find_stalled, notify_anomalies
from .forecast import chart_payload, estimate_eta
from .insights import apply_search, parse_search, rule_summary, summarize, order_facts, customer_facts
from .models import MetricObservation, Order, OrderEvent
from .observations import record_observation

User = get_user_model()
A = MetricObservation.Accuracy


class Base(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user("mgr1", password="pass12345", role="manager")
        self.op = User.objects.create_user("op1", password="pass12345", role="operator", first_name="Omar")
        self.customer = Customer.objects.create(name="Saeed")
        self.other = Customer.objects.create(name="Bilal")
        self.channel = Channel.objects.create(customer=self.customer, name="Saeed TV", url="https://www.youtube.com/@s")
        self.channel2 = Channel.objects.create(customer=self.other, name="Bilal TV", url="https://www.youtube.com/@b")
        self.now = timezone.now()

    def order(self, customer=None, channel=None, status=Order.Status.MONITORING, baseline=1000, inc=500, **kw):
        order = Order.objects.create(
            customer=customer or self.customer, channel=channel or self.channel,
            requested_increase=inc, baseline_count=baseline, created_by=self.manager,
            baseline_recorded_at=self.now - timedelta(days=10), assigned_to=self.op, **kw,
        )
        Order.objects.filter(pk=order.pk).update(status=status)
        order.refresh_from_db()
        return order

    def obs(self, order, count, hours_ago, accuracy=A.EXACT):
        when = self.now - timedelta(hours=hours_ago)
        MetricObservation.objects.create(
            order=order, observed_count=count, observed_at=when, source="manual", accuracy=accuracy
        )
        return when


class ForecastTests(Base):
    def set_latest(self, order, count, hours_ago):
        Order.objects.filter(pk=order.pk).update(
            latest_observed_count=count, latest_observed_at=self.now - timedelta(hours=hours_ago)
        )
        order.refresh_from_db()

    def test_no_data(self):
        order = self.order()
        self.assertEqual(estimate_eta(order, self.now).state, "no_data")

    def test_estimate_from_trend(self):
        order = self.order()                       # baseline 1000, target 1500
        self.obs(order, 1100, 48)
        self.obs(order, 1200, 24)
        self.obs(order, 1300, 0.01)
        self.set_latest(order, 1300, 0.01)
        eta = estimate_eta(order, self.now)
        self.assertEqual(eta.state, "ok")
        self.assertIn("about 2 days", eta.message)           # 200 left at ~100/day
        self.assertGreater(eta.rate_per_day, 80)

    def test_stalled_and_declining(self):
        order = self.order()
        self.obs(order, 1200, 48); self.obs(order, 1200, 1)
        self.set_latest(order, 1200, 1)
        self.assertEqual(estimate_eta(order, self.now).state, "stalled")
        order2 = self.order(channel=self.channel)
        self.obs(order2, 1300, 48); self.obs(order2, 1250, 1)
        self.set_latest(order2, 1250, 1)
        self.assertEqual(estimate_eta(order2, self.now).state, "declining")

    def test_reached_and_insufficient(self):
        order = self.order()
        self.set_latest(order, 1500, 1)
        self.assertEqual(estimate_eta(order, self.now).state, "reached")
        order2 = self.order()
        MetricObservation.objects.all().delete()
        Order.objects.filter(pk=order2.pk).update(baseline_recorded_at=self.now - timedelta(minutes=5))
        self.obs(order2, 1010, 0.01)
        self.set_latest(order2, 1010, 0.01)
        order2.refresh_from_db()
        self.assertEqual(estimate_eta(order2, self.now).state, "insufficient")

    def test_unavailable_observations_ignored_and_chart_payload(self):
        order = self.order()
        self.obs(order, 1100, 48)
        MetricObservation.objects.create(order=order, observed_count=None, observed_at=self.now,
                                         source="manual", accuracy=A.UNAVAILABLE)
        self.obs(order, 1300, 0.01)
        self.set_latest(order, 1300, 0.01)
        data = chart_payload(order, self.now)
        self.assertEqual(data["target"], 1500)
        self.assertEqual(data["values"][:3], [1000, 1100, 1300])    # baseline + 2 real observations
        self.assertEqual(len(data["labels"]), len(data["projection"]))
        hidden = chart_payload(order, self.now, include_estimate=False)
        self.assertEqual(hidden["eta"]["state"], "hidden")

    def test_order_page_shows_chart_and_estimate(self):
        order = self.order()
        self.client.force_login(self.manager)
        response = self.client.get(reverse("orders:detail", args=[order.pk]))
        self.assertContains(response, "chartGrowth")
        self.assertContains(response, "order-chart-data")


class AnomalyTests(Base):
    def test_drop_alert_and_dedupe(self):
        order = self.order()
        self.assertFalse(check_drop(order, 1000, 990, self.now))        # 1% < 3%
        self.assertTrue(check_drop(order, 1000, 900, self.now))
        self.assertTrue(check_drop(order, 1000, 900, self.now))
        self.assertEqual(Notification.objects.filter(kind=Notification.Kind.ANOMALY, recipient=self.manager).count(), 1)
        self.assertEqual(OrderEvent.objects.filter(order=order, action="Count dropped").count(), 2)
        self.assertFalse(check_drop(order, None, 900, self.now))
        self.assertFalse(check_drop(order, 900, 950, self.now))

    def test_record_observation_raises_drop_alert(self):
        order = self.order()
        record_observation(order, count=1200, accuracy=A.EXACT, source="manual", user=self.manager)
        record_observation(order, count=1100, accuracy=A.EXACT, source="manual", user=self.manager)
        self.assertTrue(Notification.objects.filter(kind=Notification.Kind.ANOMALY, message__contains="dropped").exists())

    def test_stalled_detection(self):
        stuck = self.order()
        self.obs(stuck, 1200, 60); self.obs(stuck, 1200, 1)
        Order.objects.filter(pk=stuck.pk).update(latest_observed_count=1200, latest_observed_at=self.now - timedelta(hours=1))
        growing = self.order(channel=self.channel2, customer=self.other)
        self.obs(growing, 1100, 60); self.obs(growing, 1300, 1)
        Order.objects.filter(pk=growing.pk).update(latest_observed_count=1300, latest_observed_at=self.now - timedelta(hours=1))
        found = [o.pk for o, _ in find_stalled(self.now, 48)]
        self.assertEqual(found, [stuck.pk])
        self.assertEqual(notify_anomalies(self.now), 1)
        self.assertEqual(notify_anomalies(self.now), 0)            # not repeated within 24 hours

    def test_paused_and_dead_checks_are_not_stalled(self):
        paused = self.order(status=Order.Status.PAUSED)
        self.obs(paused, 1200, 60); self.obs(paused, 1200, 1)
        Order.objects.filter(pk=paused.pk).update(latest_observed_count=1200, latest_observed_at=self.now - timedelta(hours=1))
        silent = self.order(channel=self.channel2, customer=self.other)
        self.obs(silent, 1200, 100)
        Order.objects.filter(pk=silent.pk).update(latest_observed_count=1200, latest_observed_at=self.now - timedelta(hours=100))
        self.assertEqual(find_stalled(self.now, 48), [])


class SummaryAndSearchTests(Base):
    def test_rule_summaries_english_and_urdu(self):
        self.order(status=Order.Status.TARGET_OBSERVED)
        self.order(status=Order.Status.MONITORING)
        self.order(status=Order.Status.PAUSED)
        facts = customer_facts(self.customer)
        en = rule_summary("group", facts, "en")
        ur = rule_summary("group", facts, "ur")
        self.assertIn("Saeed has 3 orders", en)
        self.assertIn("review pending", en)
        self.assertIn("Saeed ke 3 orders hain", ur)
        self.assertIn("review baqi", ur)

    def test_order_summary_has_no_private_data(self):
        order = self.order(notes="secret note about client")
        facts = order_facts(order)
        text = rule_summary("order", facts, "en")
        self.assertNotIn("secret", str(facts)); self.assertNotIn("secret", text)
        self.assertIn(order.order_number, text)

    def test_summary_falls_back_without_key(self):
        facts = customer_facts(self.customer)
        with override_settings(ANTHROPIC_API_KEY=""):
            text, source, note = summarize("group", facts, "en", self.manager)
        self.assertEqual(source, "rules")

    def test_summary_uses_ai_when_configured(self):
        facts = customer_facts(self.customer)
        with override_settings(ANTHROPIC_API_KEY="test-key"), patch("core.ai.ask", return_value="AI TEXT") as mocked:
            text, source, _ = summarize("group", facts, "ur", self.manager)
        self.assertEqual((text, source), ("AI TEXT", "ai"))
        self.assertIn("Roman Urdu", mocked.call_args.args[1])
        self.assertNotIn("test-key", mocked.call_args.args[1])

    def test_summary_ai_failure_falls_back(self):
        facts = customer_facts(self.customer)
        with override_settings(ANTHROPIC_API_KEY="k"), patch("core.ai.ask", side_effect=ai.AIError("The AI service returned an error (500).")):
            text, source, note = summarize("group", facts, "en", self.manager)
        self.assertEqual(source, "rules"); self.assertIn("500", note)

    def test_ai_hourly_limit(self):
        facts = customer_facts(self.customer)
        with override_settings(ANTHROPIC_API_KEY="k", AI_HOURLY_LIMIT=1), patch("core.ai.ask", return_value="X"):
            self.assertEqual(summarize("group", facts, "en", self.manager)[1], "ai")
            self.assertEqual(summarize("group", facts, "en", self.manager)[1], "rules")

    def test_ai_key_never_reaches_error_text(self):
        class Resp:
            status_code = 401
            text = "invalid key sk-secret"
        with override_settings(ANTHROPIC_API_KEY="sk-secret"), patch("requests.post", return_value=Resp()):
            with self.assertRaises(ai.AIError) as ctx:
                ai.ask("s", "p")
        self.assertNotIn("sk-secret", str(ctx.exception))

    def test_gemini_request_and_parse(self):
        class Resp:
            status_code = 200
            def json(self):
                return {"candidates": [{"content": {"parts": [{"text": "Hello "}, {"text": "team"}]}}]}
        with override_settings(AI_PROVIDER="gemini", GEMINI_API_KEY="g-key", GEMINI_MODEL="gemini-2.5-flash",
                               ANTHROPIC_API_KEY=""), patch("requests.post", return_value=Resp()) as post:
            self.assertEqual(ai.provider(), "gemini")
            self.assertEqual(ai.ask("sys", "prompt", max_tokens=50), "Hello team")
        url = post.call_args.args[0]
        self.assertIn("gemini-2.5-flash:generateContent", url)
        self.assertEqual(post.call_args.kwargs["headers"]["x-goog-api-key"], "g-key")
        self.assertNotIn("g-key", url)
        self.assertEqual(post.call_args.kwargs["json"]["generationConfig"]["maxOutputTokens"], 50)

    def test_gemini_limit_and_blocked_answers(self):
        class Limited:
            status_code = 429
            text = "quota g-key"
        class Blocked:
            status_code = 200
            def json(self):
                return {"candidates": [{"finishReason": "SAFETY"}]}
        with override_settings(GEMINI_API_KEY="g-key", ANTHROPIC_API_KEY="", AI_PROVIDER=""):
            with patch("requests.post", return_value=Limited()):
                with self.assertRaises(ai.AIError) as ctx:
                    ai.ask("s", "p")
            self.assertIn("429", str(ctx.exception))
            self.assertNotIn("g-key", str(ctx.exception))
            with patch("requests.post", return_value=Blocked()):
                with self.assertRaises(ai.AIError):
                    ai.ask("s", "p")

    def test_provider_choice(self):
        with override_settings(AI_PROVIDER="", ANTHROPIC_API_KEY="", GEMINI_API_KEY=""):
            self.assertIsNone(ai.provider()); self.assertFalse(ai.is_configured())
        with override_settings(AI_PROVIDER="", ANTHROPIC_API_KEY="a", GEMINI_API_KEY="g"):
            self.assertEqual(ai.provider(), "anthropic")
        with override_settings(AI_PROVIDER="gemini", ANTHROPIC_API_KEY="a", GEMINI_API_KEY="g"):
            self.assertEqual(ai.provider(), "gemini")
        with override_settings(AI_PROVIDER="gemini", GEMINI_API_KEY=""):
            self.assertFalse(ai.is_configured())

    def test_summary_endpoint(self):
        order = self.order()
        self.client.force_login(self.manager)
        for url in (
            reverse("core:ai_summary_item", args=["order", order.pk]),
            reverse("core:ai_summary_item", args=["customer", self.customer.pk]),
            reverse("core:ai_summary", args=["portfolio"]),
        ):
            data = self.client.post(url, {"lang": "en"}).json()
            self.assertEqual(data["source"], "rules")
            self.assertTrue(data["summary"])
        self.assertEqual(self.client.get(reverse("core:ai_summary", args=["portfolio"])).status_code, 405)
        self.assertEqual(self.client.post(reverse("core:ai_summary", args=["nope"])).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.post(reverse("core:ai_summary", args=["portfolio"])).status_code, 302)

    def test_search_parsing(self):
        p = parse_search("paused orders for Bilal", self.manager)
        self.assertEqual(p.statuses, {Order.Status.PAUSED})
        self.assertEqual(p.customer_names, ["Bilal"])
        p = parse_search("high priority review", self.manager)
        self.assertEqual(p.priority, "high"); self.assertIn(Order.Status.TARGET_OBSERVED, p.statuses)
        p = parse_search("pending review", self.manager)
        self.assertEqual(p.statuses, {Order.Status.PENDING_REVIEW})
        p = parse_search("saeed ke ruke hue orders aaj", self.manager)
        self.assertEqual(p.customer_names, ["Saeed"]); self.assertEqual(p.since_label, "today")
        p = parse_search("assigned to omar stalled", self.manager)
        self.assertEqual(p.staff_names, ["Omar"]); self.assertTrue(p.stalled)
        self.assertFalse(parse_search("blah blah", self.manager).understood)

    def test_search_filters_orders(self):
        a = self.order(status=Order.Status.PAUSED)
        self.order(status=Order.Status.MONITORING)
        b = self.order(customer=self.other, channel=self.channel2, status=Order.Status.PAUSED)
        parsed = parse_search("paused Bilal", self.manager)
        ids = set(apply_search(Order.objects.all(), parsed, self.manager).values_list("pk", flat=True))
        self.assertEqual(ids, {b.pk})
        parsed = parse_search("paused", self.manager)
        ids = set(apply_search(Order.objects.all(), parsed, self.manager).values_list("pk", flat=True))
        self.assertEqual(ids, {a.pk, b.pk})

    def test_orders_page_smart_search(self):
        self.order(status=Order.Status.PAUSED)
        self.client.force_login(self.manager)
        response = self.client.get(reverse("orders:list"), {"ask": "paused orders for Saeed"})
        self.assertContains(response, "Status: Paused")
        self.assertContains(response, "Customer: Saeed")
        bad = self.client.get(reverse("orders:list"), {"ask": "zzz qqq"})
        self.assertContains(bad, "was not understood")

    def test_ai_search_fallback_is_validated(self):
        reply = '{"statuses": ["paused", "hack"], "priority": "high", "customer": "Bilal", "created": "week"}'
        with override_settings(ANTHROPIC_API_KEY="k"), patch("core.ai.ask", return_value=reply):
            p = parse_search("the usual suspects from china", self.manager)
        self.assertEqual(p.statuses, {Order.Status.PAUSED})
        self.assertEqual(p.priority, "high"); self.assertEqual(p.customer_names, ["Bilal"])
        self.assertTrue(p.used_ai)

    def test_dashboard_shows_ai_and_attention(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse("core:dashboard"))
        self.assertContains(response, "AI insights")
        self.assertContains(response, "Needs attention")
