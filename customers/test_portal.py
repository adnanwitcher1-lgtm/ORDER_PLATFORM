from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog
from orders.models import MetricObservation, Order
from reports.client_report import client_orders, render_pdf
from reports.delivery import DeliveryError, send_customer_report
from ytchannels.models import Channel

from .models import Customer

User = get_user_model()


class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.manager = User.objects.create_user("mgr1", password="pass12345", role="manager")
        self.operator = User.objects.create_user("op1", password="pass12345", role="operator")
        self.customer = Customer.objects.create(name="Saeed", email="saeed@example.com", phone="0300 1234567")
        self.other = Customer.objects.create(name="Bilal")
        self.channel = Channel.objects.create(customer=self.customer, name="Saeed TV", url="https://www.youtube.com/@s")
        self.channel2 = Channel.objects.create(customer=self.other, name="Bilal TV", url="https://www.youtube.com/@b")

    def make_order(self, status, customer=None, channel=None, notes="", latest=None):
        order = Order.objects.create(
            customer=customer or self.customer, channel=channel or self.channel,
            requested_increase=500, baseline_count=1000, created_by=self.manager,
            assigned_to=self.operator, notes=notes,
        )
        Order.objects.filter(pk=order.pk).update(
            status=status, latest_observed_count=latest,
            latest_observed_at=timezone.now() if latest else None,
        )
        order.refresh_from_db()
        return order


class PortalTests(Base):
    def go_live(self):
        self.customer.new_portal_token()
        self.customer.save()
        return reverse("portal:home", args=[self.customer.portal_token])

    def test_token_is_long_random_and_unique(self):
        self.customer.new_portal_token()
        self.assertGreaterEqual(len(self.customer.portal_token), 40)
        old = self.customer.portal_token
        self.customer.new_portal_token()
        self.assertNotEqual(old, self.customer.portal_token)

    def test_portal_shows_client_safe_data_only(self):
        visible = self.make_order(Order.Status.MONITORING, notes="INTERNAL-NOTE-XYZ", latest=1200)
        self.make_order(Order.Status.DRAFT, notes="draft-hidden")
        self.make_order(Order.Status.PENDING_REVIEW)
        self.make_order(Order.Status.MONITORING, customer=self.other, channel=self.channel2)
        url = self.go_live()
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, visible.order_number)
        self.assertContains(response, "In progress")
        body = response.content.decode()
        for secret in ("INTERNAL-NOTE-XYZ", "op1", "mgr1", "Bilal", "draft-hidden", "Monitoring"):
            self.assertNotIn(secret, body)
        self.assertEqual(len(response.context["rows"]), 1)            # no draft, no pending, no other customer
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(response["Referrer-Policy"], "no-referrer")

    def test_order_page_and_cross_customer_isolation(self):
        mine = self.make_order(Order.Status.MONITORING, latest=1100)
        theirs = self.make_order(Order.Status.MONITORING, customer=self.other, channel=self.channel2)
        draft = self.make_order(Order.Status.DRAFT)
        url = self.go_live()
        token = self.customer.portal_token
        ok = self.client.get(reverse("portal:order", args=[token, mine.pk]))
        self.assertContains(ok, "chartGrowth")
        self.assertEqual(self.client.get(reverse("portal:order", args=[token, theirs.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("portal:order", args=[token, draft.pk])).status_code, 404)

    def test_bad_disabled_archived_tokens_are_404(self):
        self.assertEqual(self.client.get(reverse("portal:home", args=["nope"])).status_code, 404)
        url = self.go_live()
        self.assertEqual(self.client.get(url).status_code, 200)
        self.customer.disable_portal(); self.customer.save()
        self.assertEqual(self.client.get(url).status_code, 404)
        self.customer.portal_enabled = True; self.customer.status = Customer.Status.ARCHIVED; self.customer.save()
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_old_link_stops_working_after_regenerate(self):
        old = self.go_live()
        self.customer.new_portal_token(); self.customer.save()
        self.assertEqual(self.client.get(old).status_code, 404)

    def test_read_only_and_throttled(self):
        url = self.go_live()
        self.assertEqual(self.client.post(url).status_code, 405)
        for i in range(30):
            self.client.get(reverse("portal:home", args=[f"bad{i}"]))
        self.assertEqual(self.client.get(url).status_code, 404)          # this address is now throttled

    def test_last_viewed_is_recorded(self):
        url = self.go_live()
        self.client.get(url)
        self.customer.refresh_from_db()
        self.assertIsNotNone(self.customer.portal_last_viewed_at)

    def test_portal_pdf(self):
        self.make_order(Order.Status.MONITORING, latest=1200)
        url = self.go_live()
        response = self.client.get(reverse("portal:report", args=[self.customer.portal_token]))
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))

    @override_settings(PORTAL_SHOW_ETA=False)
    def test_eta_can_be_hidden(self):
        order = self.make_order(Order.Status.MONITORING, latest=1200)
        self.go_live()
        response = self.client.get(reverse("portal:order", args=[self.customer.portal_token, order.pk]))
        self.assertNotContains(response, "Estimate:")
        self.assertEqual(response.context["chart_data"]["eta"]["state"], "hidden")

    def test_staff_controls_and_permissions(self):
        enable = reverse("customers:portal_action", args=[self.customer.pk, "enable"])
        self.client.force_login(self.operator)
        self.assertEqual(self.client.post(enable).status_code, 403)           # operators cannot manage
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(enable).status_code, 405)
        self.client.post(enable)
        self.customer.refresh_from_db()
        self.assertTrue(self.customer.portal_is_live)
        first = self.customer.portal_token
        self.client.post(reverse("customers:portal_action", args=[self.customer.pk, "regenerate"]))
        self.customer.refresh_from_db()
        self.assertNotEqual(first, self.customer.portal_token)
        self.client.post(reverse("customers:portal_action", args=[self.customer.pk, "disable"]))
        self.customer.refresh_from_db()
        self.assertFalse(self.customer.portal_is_live)
        self.assertEqual(AuditLog.objects.filter(entity_type="Customer", action__startswith="portal").count(), 3)

    def test_customer_page_shows_link_and_whatsapp(self):
        self.go_live()
        self.client.force_login(self.manager)
        response = self.client.get(reverse("customers:detail", args=[self.customer.pk]))
        self.assertContains(response, self.customer.portal_token)
        self.assertContains(response, "https://wa.me/923001234567")
        self.assertContains(response, "Download PDF report")


class WhatsAppNumberTests(TestCase):
    def test_number_normalisation(self):
        c = Customer(name="x", phone="0300-1234567")
        self.assertEqual(c.whatsapp_number(), "923001234567")
        self.assertEqual(Customer(name="x", phone="+92 300 1234567").whatsapp_number(), "923001234567")
        self.assertEqual(Customer(name="x", phone="0092 300 1234567").whatsapp_number(), "923001234567")
        self.assertEqual(Customer(name="x", phone="12").whatsapp_number(), "")
        self.assertEqual(Customer(name="x", phone="").whatsapp_link("hi"), "")
        self.assertIn("text=hello%20world", Customer(name="x", phone="03001234567").whatsapp_link("hello world"))


class ReportTests(Base):
    def test_client_orders_hide_internal_statuses(self):
        self.make_order(Order.Status.DRAFT)
        self.make_order(Order.Status.COMPLETED, latest=1500)
        rows = client_orders(self.customer)
        self.assertEqual([r["status_label"] for r in rows], ["Completed"])

    def test_pdf_is_generated_with_and_without_orders(self):
        self.assertTrue(render_pdf(self.customer, []).startswith(b"%PDF"))
        self.make_order(Order.Status.MONITORING, latest=1300)
        self.assertTrue(render_pdf(self.customer).startswith(b"%PDF"))

    def test_staff_download(self):
        self.make_order(Order.Status.MONITORING, latest=1300)
        self.client.force_login(self.operator)
        response = self.client.get(reverse("customers:report_pdf", args=[self.customer.pk]))
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.client.logout()
        self.assertEqual(self.client.get(reverse("customers:report_pdf", args=[self.customer.pk])).status_code, 302)

    def test_email_report(self):
        self.make_order(Order.Status.MONITORING, latest=1300)
        self.customer.new_portal_token(); self.customer.save()
        send_customer_report(self.customer, user=self.manager)
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ["saeed@example.com"])
        self.assertEqual(message.attachments[0][2], "application/pdf")
        self.assertIn(self.customer.portal_token, message.body)
        self.customer.refresh_from_db()
        self.assertIsNotNone(self.customer.last_report_sent_at)

    def test_email_errors_are_clear(self):
        no_email = Customer.objects.create(name="NoMail")
        with self.assertRaises(DeliveryError):
            send_customer_report(no_email)
        with self.assertRaises(DeliveryError):
            send_customer_report(self.customer)                    # no orders yet

    def test_email_button_permissions(self):
        self.make_order(Order.Status.MONITORING, latest=1300)
        url = reverse("customers:report_email", args=[self.customer.pk])
        self.client.force_login(self.operator)
        self.assertEqual(self.client.post(url).status_code, 403)
        self.client.force_login(self.manager)
        self.client.post(url)
        self.assertEqual(len(mail.outbox), 1)

    def test_weekly_command(self):
        self.make_order(Order.Status.MONITORING, latest=1300)
        out = StringIO()
        call_command("send_client_reports", stdout=out)                # weekly not enabled yet
        self.assertEqual(len(mail.outbox), 0)
        Customer.objects.filter(pk=self.customer.pk).update(weekly_report_enabled=True)
        call_command("send_client_reports", "--dry-run", stdout=out)
        self.assertEqual(len(mail.outbox), 0)
        call_command("send_client_reports", stdout=out)
        self.assertEqual(len(mail.outbox), 1)
        call_command("send_client_reports", stdout=out)                # sent within 6 days: skipped
        self.assertEqual(len(mail.outbox), 1)
        call_command("send_client_reports", "--force", stdout=out)
        self.assertEqual(len(mail.outbox), 2)

    def test_weekly_requires_email(self):
        from .forms import CustomerForm
        form = CustomerForm({"name": "A", "email": "", "phone": "", "weekly_report_enabled": "on", "notes": ""})
        self.assertFalse(form.is_valid())
        self.assertIn("email", form.errors)
