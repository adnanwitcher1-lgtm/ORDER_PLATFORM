from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from customers.models import Customer
from notifications.models import Notification
from ytchannels.models import Channel

from .calculations import calculate_progress, calculate_target
from .models import MetricObservation, Order, OrderEvent
from .monitoring import due_orders, notify_overdue_reviews, run_due_checks
from .observations import ObservationError, can_record_observation, record_observation
from .workflow import WorkflowError, apply_transition, assign_order, available_transitions
from .youtube import FetchResult, fetch_many, fetch_subscriber_count, resolve_lookup


class CalculationTests(SimpleTestCase):
    def test_tc01_target(self):
        self.assertEqual(calculate_target(500, 100), 600)

    def test_tc02_partial_progress(self):
        p = calculate_progress(500, 100, 560)
        self.assertEqual(p.observed_increase, 60)
        self.assertEqual(p.remaining_gap, 40)
        self.assertEqual(p.progress_percent, 60)
        self.assertFalse(p.target_reached)

    def test_tc03_target_reached(self):
        p = calculate_progress(500, 100, 600)
        self.assertTrue(p.target_reached)
        self.assertEqual(p.remaining_gap, 0)
        self.assertEqual(p.progress_percent, 100)

    def test_overshoot_is_clamped(self):
        p = calculate_progress(500, 100, 650)
        self.assertTrue(p.target_reached)
        self.assertEqual(p.remaining_gap, 0)
        self.assertEqual(p.progress_percent, 100)
        self.assertEqual(p.observed_increase, 150)

    def test_tc04_unavailable_metric(self):
        p = calculate_progress(500, 100, None)
        self.assertFalse(p.has_observation)
        self.assertFalse(p.target_reached)
        self.assertIsNone(p.observed_increase)
        self.assertEqual(p.progress_percent, 0)

    def test_below_baseline(self):
        p = calculate_progress(500, 100, 480)
        self.assertTrue(p.below_baseline)
        self.assertEqual(p.observed_increase, -20)
        self.assertEqual(p.progress_percent, 0)
        self.assertEqual(p.remaining_gap, 120)

    def test_floor_never_shows_false_100(self):
        p = calculate_progress(500, 300, 799)
        self.assertEqual(p.progress_percent, 99)

    def test_invalid_quantity_rejected(self):
        with self.assertRaises(ValueError):
            calculate_target(500, 0)
        with self.assertRaises(ValueError):
            calculate_target(500, -5)


class OrderModelTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user("sales1", password="pass12345", role="sales")
        self.customer = Customer.objects.create(name="Ali")
        self.other_customer = Customer.objects.create(name="Bilal")
        self.channel = Channel.objects.create(
            customer=self.customer, name="Ali TV", url="https://www.youtube.com/@alitv"
        )

    def make_order(self, **kwargs):
        data = dict(
            customer=self.customer, channel=self.channel,
            requested_increase=100, baseline_count=500, created_by=self.user,
        )
        data.update(kwargs)
        return Order.objects.create(**data)

    def test_target_saved_on_create(self):
        order = self.make_order()
        self.assertEqual(order.target_count, 600)
        self.assertEqual(order.order_number, f"ORD-{order.pk:05d}")

    def test_draft_recalculates_target(self):
        order = self.make_order()
        order.requested_increase = 200
        order.save()
        self.assertEqual(order.target_count, 700)

    def test_approved_order_target_is_locked(self):
        order = self.make_order(status=Order.Status.APPROVED)
        order.baseline_count = 999
        order.save()
        order.refresh_from_db()
        self.assertEqual(order.target_count, 600)

    def test_channel_must_belong_to_customer(self):
        order = Order(
            customer=self.other_customer, channel=self.channel,
            requested_increase=100, baseline_count=500,
        )
        with self.assertRaises(ValidationError):
            order.full_clean()

    def test_zero_increase_rejected(self):
        order = Order(
            customer=self.customer, channel=self.channel,
            requested_increase=0, baseline_count=500,
        )
        with self.assertRaises(ValidationError):
            order.full_clean()

    def test_progress_property(self):
        order = self.make_order(latest_observed_count=560)
        self.assertEqual(order.progress.progress_percent, 60)
        self.assertEqual(order.progress.remaining_gap, 40)


class WorkflowTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.sales = User.objects.create_user("sales1", password="pass12345", role="sales")
        self.manager = User.objects.create_user("mgr1", password="pass12345", role="manager")
        self.op1 = User.objects.create_user("op1", password="pass12345", role="operator")
        self.op2 = User.objects.create_user("op2", password="pass12345", role="operator")
        self.customer = Customer.objects.create(name="Ali")
        self.channel = Channel.objects.create(
            customer=self.customer, name="Ali TV", url="https://www.youtube.com/@alitv"
        )
        self.order = Order.objects.create(
            customer=self.customer, channel=self.channel,
            requested_increase=100, baseline_count=500, created_by=self.sales,
        )

    def reload(self):
        self.order.refresh_from_db()
        return self.order

    def move_to_assigned(self):
        apply_transition(self.order, "submit", self.sales)
        apply_transition(self.order, "approve", self.manager)
        assign_order(self.order, self.op1, self.manager)
        return self.reload()

    def test_full_lifecycle(self):
        order = self.move_to_assigned()
        self.assertEqual(order.status, Order.Status.ASSIGNED)
        self.assertIsNotNone(order.approved_at)

        apply_transition(order, "start_monitoring", self.op1)
        self.assertEqual(self.reload().status, Order.Status.MONITORING)

        Order.objects.filter(pk=order.pk).update(
            status=Order.Status.TARGET_OBSERVED, latest_observed_count=600
        )
        apply_transition(order, "complete", self.manager, "Source verified")
        order = self.reload()
        self.assertEqual(order.status, Order.Status.COMPLETED)
        self.assertEqual(order.reviewed_by, self.manager)
        self.assertIsNotNone(order.reviewed_at)
        self.assertIsNotNone(order.closed_at)
        self.assertGreaterEqual(order.events.count(), 5)

    def test_sales_cannot_approve(self):
        apply_transition(self.order, "submit", self.sales)
        with self.assertRaises(WorkflowError):
            apply_transition(self.order, "approve", self.sales)
        self.assertEqual(self.reload().status, Order.Status.PENDING_REVIEW)

    def test_reason_required_for_pause(self):
        self.move_to_assigned()
        with self.assertRaises(WorkflowError):
            apply_transition(self.order, "pause", self.manager, "   ")
        apply_transition(self.order, "pause", self.manager, "Customer asked to stop")
        self.assertEqual(self.reload().status, Order.Status.PAUSED)

    def test_resume_returns_to_previous_status(self):
        self.move_to_assigned()
        apply_transition(self.order, "start_monitoring", self.op1)
        apply_transition(self.order, "pause", self.manager, "Wait")
        apply_transition(self.order, "resume", self.manager)
        self.assertEqual(self.reload().status, Order.Status.MONITORING)

    def test_operator_only_on_own_order(self):
        order = self.move_to_assigned()
        with self.assertRaises(WorkflowError):
            apply_transition(order, "start_monitoring", self.op2)
        keys_op2 = [t.key for t in available_transitions(self.op2, order)]
        keys_op1 = [t.key for t in available_transitions(self.op1, order)]
        self.assertNotIn("start_monitoring", keys_op2)
        self.assertIn("start_monitoring", keys_op1)

    def test_assignment_history(self):
        self.move_to_assigned()
        assign_order(self.order, self.op2, self.manager, "Workload balance")
        order = self.reload()
        self.assertEqual(order.assigned_to, self.op2)
        self.assertEqual(order.assignments.count(), 2)
        self.assertEqual(order.status, Order.Status.ASSIGNED)

    def test_cannot_assign_draft(self):
        with self.assertRaises(WorkflowError):
            assign_order(self.order, self.op1, self.manager)

    def test_cancel_blocks_further_actions(self):
        apply_transition(self.order, "cancel_draft", self.sales, "Galat entry")
        order = self.reload()
        self.assertEqual(order.status, Order.Status.CANCELLED)
        self.assertIsNotNone(order.closed_at)
        with self.assertRaises(WorkflowError):
            apply_transition(self.order, "submit", self.sales)
        self.assertEqual(available_transitions(self.manager, order), [])


class ObservationTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.sales = User.objects.create_user("sales1", password="pass12345", role="sales")
        self.manager = User.objects.create_user("mgr1", password="pass12345", role="manager")
        self.op1 = User.objects.create_user("op1", password="pass12345", role="operator")
        self.op2 = User.objects.create_user("op2", password="pass12345", role="operator")
        self.customer = Customer.objects.create(name="Ali")
        self.channel = Channel.objects.create(
            customer=self.customer, name="Ali TV", url="https://www.youtube.com/@alitv"
        )
        self.order = Order.objects.create(
            customer=self.customer, channel=self.channel,
            requested_increase=100, baseline_count=500,
            status=Order.Status.MONITORING, assigned_to=self.op1,
        )

    def record(self, count, accuracy="exact", **kwargs):
        return record_observation(
            self.order, count=count, accuracy=accuracy,
            source=MetricObservation.Source.MANUAL, user=self.op1, **kwargs
        )

    def test_below_target_keeps_monitoring(self):
        _, order = self.record(560)
        self.assertEqual(order.status, Order.Status.MONITORING)
        self.assertEqual(order.latest_observed_count, 560)
        self.assertEqual(order.progress.progress_percent, 60)

    def test_reaching_target_moves_to_review(self):
        obs, order = self.record(600)
        self.assertEqual(order.status, Order.Status.TARGET_OBSERVED)
        self.assertTrue(order.events.filter(action="Target observed").exists())
        self.assertEqual(order.observations.count(), 1)
        self.assertIsNone(order.closed_at)

    def test_unavailable_keeps_last_valid_count(self):
        self.record(560)
        _, order = self.record(None, accuracy="unavailable", note="Count hidden")
        self.assertEqual(order.latest_observed_count, 560)
        self.assertEqual(order.latest_observed_accuracy, "exact")
        self.assertEqual(order.status, Order.Status.MONITORING)
        self.assertEqual(order.observations.count(), 2)

    def test_unavailable_never_completes(self):
        _, order = self.record(None, accuracy="unavailable", note="Hidden")
        self.assertIsNone(order.latest_observed_count)
        self.assertEqual(order.status, Order.Status.MONITORING)

    def test_approximate_flag_saved(self):
        _, order = self.record(560, accuracy="approximate")
        self.assertEqual(order.latest_observed_accuracy, "approximate")

    def test_count_required_for_valid_observation(self):
        with self.assertRaises(ObservationError):
            self.record(None, accuracy="exact")

    def test_cannot_record_on_draft(self):
        self.order.status = Order.Status.DRAFT
        self.order.save()
        with self.assertRaises(ObservationError):
            self.record(560)

    def test_permissions(self):
        self.assertTrue(can_record_observation(self.manager, self.order))
        self.assertTrue(can_record_observation(self.op1, self.order))
        self.assertFalse(can_record_observation(self.op2, self.order))
        self.assertFalse(can_record_observation(self.sales, self.order))


class YouTubeTests(SimpleTestCase):
    def channel(self, url, channel_id=""):
        return Channel(url=url, youtube_channel_id=channel_id)

    def fake_response(self, payload, status=200):
        response = MagicMock(status_code=status)
        response.json.return_value = payload
        return response

    def test_resolve_lookup(self):
        self.assertEqual(resolve_lookup(self.channel("https://www.youtube.com/@alitv")), {"forHandle": "@alitv"})
        self.assertEqual(resolve_lookup(self.channel("https://www.youtube.com/channel/UC123")), {"id": "UC123"})
        self.assertEqual(resolve_lookup(self.channel("https://www.youtube.com/user/ali")), {"forUsername": "ali"})
        self.assertEqual(resolve_lookup(self.channel("https://www.youtube.com/@x", "UC999")), {"id": "UC999"})
        self.assertIsNone(resolve_lookup(self.channel("https://www.youtube.com/c/custom")))

    @override_settings(YOUTUBE_API_KEY="")
    def test_no_key(self):
        result = fetch_subscriber_count(self.channel("https://www.youtube.com/@alitv"))
        self.assertEqual(result.api_status, "not_configured")
        self.assertIsNone(result.count)

    @override_settings(YOUTUBE_API_KEY="test-key")
    @patch("orders.youtube.requests.get")
    def test_hidden_count(self, mock_get):
        mock_get.return_value = self.fake_response(
            {"items": [{"statistics": {"hiddenSubscriberCount": True}}]}
        )
        result = fetch_subscriber_count(self.channel("https://www.youtube.com/@alitv"))
        self.assertEqual(result.api_status, "hidden")
        self.assertEqual(result.accuracy, "unavailable")

    @override_settings(YOUTUBE_API_KEY="test-key")
    @patch("orders.youtube.requests.get")
    def test_large_count_is_approximate(self, mock_get):
        mock_get.return_value = self.fake_response(
            {"items": [{"statistics": {"subscriberCount": "12300"}}]}
        )
        result = fetch_subscriber_count(self.channel("https://www.youtube.com/@alitv"))
        self.assertEqual(result.count, 12300)
        self.assertEqual(result.accuracy, "approximate")

    @override_settings(YOUTUBE_API_KEY="test-key")
    @patch("orders.youtube.requests.get")
    def test_channel_not_found(self, mock_get):
        mock_get.return_value = self.fake_response({"items": []})
        result = fetch_subscriber_count(self.channel("https://www.youtube.com/@missing"))
        self.assertEqual(result.api_status, "not_found")

    @override_settings(YOUTUBE_API_KEY="test-key")
    @patch("orders.youtube.requests.get")
    def test_fetch_many_maps_by_id(self, mock_get):
        mock_get.return_value = self.fake_response(
            {"items": [{"id": "UC1", "statistics": {"subscriberCount": "500"}}]}
        )
        results = fetch_many(["UC1", "UC2", "UC1"])
        self.assertEqual(mock_get.call_count, 1)
        self.assertEqual(results["UC1"].count, 500)
        self.assertEqual(results["UC1"].accuracy, "exact")
        self.assertEqual(results["UC2"].api_status, "not_found")


@override_settings(
    YOUTUBE_API_KEY="test-key",
    MONITORING_INTERVAL_MINUTES=60,
    MONITORING_RETRY_BASE_MINUTES=5,
    MONITORING_MAX_FAILURES=5,
    REVIEW_OVERDUE_HOURS=24,
)
class MonitoringTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.manager = User.objects.create_user("mgr1", password="pass12345", role="manager")
        self.op1 = User.objects.create_user("op1", password="pass12345", role="operator")
        self.customer = Customer.objects.create(name="Ali")
        self.channel = Channel.objects.create(
            customer=self.customer, name="Ali TV",
            url="https://www.youtube.com/@alitv", youtube_channel_id="UC1",
        )
        self.order = self.make_order()

    def make_order(self, channel=None, **kwargs):
        data = dict(
            customer=self.customer, channel=channel or self.channel,
            requested_increase=100, baseline_count=500,
            status=Order.Status.MONITORING, assigned_to=self.op1,
        )
        data.update(kwargs)
        return Order.objects.create(**data)

    def ok(self, count=560):
        return FetchResult(count, "exact", "ok", "", "UC1")

    def fail(self, status="error"):
        return FetchResult(None, "unavailable", status, "boom")

    def run_with(self, result):
        with patch("orders.monitoring.fetch_many", return_value={"UC1": result}):
            return run_due_checks()

    def test_due_selection(self):
        now = timezone.now()
        self.make_order(status=Order.Status.PAUSED)
        self.make_order(status=Order.Status.TARGET_OBSERVED)
        self.make_order(next_check_at=now + timedelta(hours=1))
        self.assertEqual(list(due_orders(now)), [self.order])

    def test_success_records_and_schedules(self):
        summary = self.run_with(self.ok(560))
        self.order.refresh_from_db()
        self.assertEqual(summary["ok"], 1)
        self.assertEqual(self.order.latest_observed_count, 560)
        self.assertEqual(self.order.check_failures, 0)
        self.assertGreater(self.order.next_check_at, timezone.now() + timedelta(minutes=30))
        observation = self.order.observations.get()
        self.assertEqual(observation.source, "youtube_api")
        self.assertIsNone(observation.recorded_by)

    def test_target_reached_notifies_manager_and_operator(self):
        self.run_with(self.ok(600))
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.TARGET_OBSERVED)
        kind = Notification.Kind.TARGET_OBSERVED
        self.assertTrue(Notification.objects.filter(recipient=self.manager, kind=kind).exists())
        self.assertTrue(Notification.objects.filter(recipient=self.op1, kind=kind).exists())

    def test_failure_uses_backoff(self):
        self.run_with(self.fail("error"))
        self.order.refresh_from_db()
        self.assertEqual(self.order.check_failures, 1)
        self.assertEqual(self.order.status, Order.Status.MONITORING)
        delay = self.order.next_check_at - timezone.now()
        self.assertTrue(timedelta(minutes=4) < delay < timedelta(minutes=6))
        self.assertEqual(self.order.observations.get().accuracy, "unavailable")

    def test_max_failures_moves_to_exception(self):
        Order.objects.filter(pk=self.order.pk).update(check_failures=4)
        summary = self.run_with(self.fail("error"))
        self.order.refresh_from_db()
        self.assertEqual(summary["exceptions"], 1)
        self.assertEqual(self.order.status, Order.Status.EXCEPTION)
        self.assertEqual(self.order.check_failures, 0)
        self.assertIsNone(self.order.next_check_at)
        self.assertTrue(OrderEvent.objects.filter(order=self.order, action="Auto-check stopped").exists())
        self.assertTrue(
            Notification.objects.filter(recipient=self.manager, kind=Notification.Kind.EXCEPTION).exists()
        )

    def test_quota_stops_without_counting_failure(self):
        summary = self.run_with(self.fail("quota"))
        self.order.refresh_from_db()
        self.assertEqual(summary["stopped"], "quota")
        self.assertEqual(self.order.check_failures, 0)
        self.assertEqual(self.order.observations.count(), 0)
        self.assertTrue(
            Notification.objects.filter(recipient=self.manager, kind=Notification.Kind.API_ERROR).exists()
        )

    @override_settings(YOUTUBE_API_KEY="")
    def test_not_configured_changes_nothing(self):
        summary = run_due_checks()
        self.order.refresh_from_db()
        self.assertEqual(summary["stopped"], "not_configured")
        self.assertIsNone(self.order.next_check_at)

    def test_handle_channel_id_is_saved(self):
        channel2 = Channel.objects.create(
            customer=self.customer, name="Handle only", url="https://www.youtube.com/@handleonly"
        )
        self.make_order(channel=channel2)
        found = FetchResult(100, "exact", "ok", "", "UCnew")
        with patch("orders.monitoring.fetch_many", return_value={"UC1": self.ok()}), \
             patch("orders.monitoring.fetch_subscriber_count", return_value=found):
            run_due_checks()
        channel2.refresh_from_db()
        self.assertEqual(channel2.youtube_channel_id, "UCnew")

    def test_overdue_review_notified_once(self):
        Order.objects.filter(pk=self.order.pk).update(status=Order.Status.TARGET_OBSERVED)
        event = OrderEvent.objects.create(
            order=self.order, action="Target observed", to_status=Order.Status.TARGET_OBSERVED
        )
        OrderEvent.objects.filter(pk=event.pk).update(created_at=timezone.now() - timedelta(hours=30))
        notify_overdue_reviews()
        notify_overdue_reviews()
        kind = Notification.Kind.REVIEW_OVERDUE
        self.assertEqual(Notification.objects.filter(recipient=self.manager, kind=kind).count(), 1)