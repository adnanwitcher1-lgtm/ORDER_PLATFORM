from io import StringIO
from unittest.mock import MagicMock, patch

import requests
from django.core.management import call_command
from django.test import override_settings
from django.urls import reverse

from audit.models import AuditLog
from orders.models import Order
from reports import whatsapp

from .models import Customer
from .test_portal import Base

CFG = dict(WHATSAPP_TOKEN="tok-secret-123", WHATSAPP_PHONE_NUMBER_ID="555", WHATSAPP_TEMPLATE_NAME="weekly_progress_report",
           WHATSAPP_TEMPLATE_LANG="en", WHATSAPP_API_VERSION="v21.0", SITE_URL="https://orders.example.com")


def ok_response(status=200, body=None):
    r = MagicMock(); r.status_code = status
    r.json.return_value = body if body is not None else {"messages": [{"id": "wamid.ABC"}]}
    return r


@override_settings(**CFG)
class WhatsAppApiTests(Base):
    def setUp(self):
        super().setUp()
        self.make_order(Order.Status.MONITORING, latest=1300)
        self.customer.new_portal_token(); self.customer.save()

    def test_payload_and_url(self):
        with patch("requests.post", return_value=ok_response()) as post:
            message_id = whatsapp.send_whatsapp_report(self.customer, user=self.manager)
        self.assertEqual(message_id, "wamid.ABC")
        args, kwargs = post.call_args
        self.assertEqual(args[0], "https://graph.facebook.com/v21.0/555/messages")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer tok-secret-123")
        payload = kwargs["json"]
        self.assertEqual(payload["to"], "923001234567")
        self.assertEqual(payload["type"], "template")
        self.assertEqual(payload["template"]["name"], "weekly_progress_report")
        params = payload["template"]["components"][0]["parameters"]
        self.assertEqual(params[0]["text"], "Saeed")
        self.assertIn(f"https://orders.example.com/portal/{self.customer.portal_token}/", params[1]["text"])
        self.customer.refresh_from_db()
        self.assertIsNotNone(self.customer.last_whatsapp_sent_at)
        self.assertTrue(AuditLog.objects.filter(action="report sent on WhatsApp").exists())

    def test_error_message_hides_token(self):
        body = {"error": {"message": "Template does not exist", "code": 132001}}
        with patch("requests.post", return_value=ok_response(400, body)):
            with self.assertRaises(whatsapp.WhatsAppError) as ctx:
                whatsapp.send_whatsapp_report(self.customer)
        self.assertIn("400", str(ctx.exception)); self.assertIn("Template does not exist", str(ctx.exception))
        self.assertNotIn("tok-secret", str(ctx.exception))
        with patch("requests.post", side_effect=requests.ConnectionError("boom tok-secret-123")):
            with self.assertRaises(whatsapp.WhatsAppError) as ctx:
                whatsapp.send_whatsapp_report(self.customer)
        self.assertNotIn("tok-secret", str(ctx.exception))

    def test_requires_number_portal_and_orders(self):
        Customer.objects.filter(pk=self.customer.pk).update(phone="")
        self.customer.refresh_from_db()
        with self.assertRaises(whatsapp.WhatsAppError):
            whatsapp.send_whatsapp_report(self.customer)
        Customer.objects.filter(pk=self.customer.pk).update(phone="0300 1234567", portal_enabled=False)
        self.customer.refresh_from_db()
        with self.assertRaisesMessage(whatsapp.WhatsAppError, "portal link"):
            whatsapp.send_whatsapp_report(self.customer)

    @override_settings(WHATSAPP_TOKEN="")
    def test_not_configured(self):
        with self.assertRaises(whatsapp.WhatsAppError):
            whatsapp.send_template("923001234567", "x", "y")

    def test_staff_button(self):
        url = reverse("customers:report_whatsapp", args=[self.customer.pk])
        self.client.force_login(self.operator)
        self.assertEqual(self.client.post(url).status_code, 403)
        self.client.force_login(self.manager)
        page = self.client.get(reverse("customers:detail", args=[self.customer.pk]))
        self.assertContains(page, "Send WhatsApp message now")
        with patch("requests.post", return_value=ok_response()):
            response = self.client.post(url, follow=True)
        self.assertContains(response, "WhatsApp message sent.")

    def test_weekly_command_sends_whatsapp_once(self):
        Customer.objects.filter(pk=self.customer.pk).update(weekly_whatsapp_enabled=True)
        out = StringIO()
        with patch("requests.post", return_value=ok_response()) as post:
            call_command("send_client_reports", stdout=out)
            call_command("send_client_reports", stdout=out)       # within 6 days: skipped
        self.assertEqual(post.call_count, 1)
        self.assertIn("Sent WhatsApp", out.getvalue())

    def test_form_requires_phone_for_whatsapp(self):
        from .forms import CustomerForm
        form = CustomerForm({"name": "A", "email": "", "phone": "", "weekly_whatsapp_enabled": "on", "notes": ""})
        self.assertFalse(form.is_valid()); self.assertIn("phone", form.errors)

    def test_integration_checker_command(self):
        out = StringIO()
        call_command("check_integrations", stdout=out)
        text = out.getvalue()
        self.assertIn("WhatsApp API", text)
        self.assertNotIn("tok-secret", text)
