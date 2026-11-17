from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from customers.forms import CustomerForm
from customers.models import Customer
from notifications.models import Notification
from reports.builders import audit_report

from .models import AuditLog
from .services import form_changes, log_audit


class AuditTests(TestCase):
    def setUp(self):
        cache.clear()
        User = get_user_model()
        self.sales = User.objects.create_user("sales1", password="pass12345", role="sales")
        self.manager = User.objects.create_user("mgr1", password="pass12345", role="manager")
        self.auditor = User.objects.create_user("aud1", password="pass12345", role="auditor")

    def post_login(self, password):
        return self.client.post(reverse("accounts:login"), {"username": "mgr1", "password": password})

    def test_log_audit_records_actor_and_ip(self):
        request = RequestFactory().get("/", REMOTE_ADDR="10.1.2.3", HTTP_USER_AGENT="TestAgent")
        request.user = self.manager
        customer = Customer.objects.create(name="Ali")
        log_audit(request, "created", customer)
        entry = AuditLog.objects.get()
        self.assertEqual(entry.actor, self.manager)
        self.assertEqual(entry.entity_type, "Customer")
        self.assertEqual(entry.entity_label, "Ali")
        self.assertEqual(entry.ip_address, "10.1.2.3")
        self.assertEqual(entry.user_agent, "TestAgent")

    def test_form_changes_redacts_notes(self):
        customer = Customer.objects.create(name="Ali", notes="old secret")
        form = CustomerForm(
            {"name": "Ali Khan", "email": "", "phone": "", "notes": "new secret"}, instance=customer
        )
        self.assertTrue(form.is_valid())
        changes = form_changes(form)
        self.assertEqual(changes["name"], ["Ali", "Ali Khan"])
        self.assertEqual(changes["notes"], "(changed)")
        self.assertNotIn("secret", str(changes))

    def test_customer_create_and_update_audited(self):
        self.client.force_login(self.sales)
        self.client.post(reverse("customers:create"), {"name": "Ali", "email": "", "phone": "", "notes": ""})
        customer = Customer.objects.get(name="Ali")
        self.assertTrue(AuditLog.objects.filter(
            action="created", entity_type="Customer", entity_id=str(customer.pk)
        ).exists())

        self.client.post(
            reverse("customers:update", args=[customer.pk]),
            {"name": "Ali Khan", "email": "", "phone": "", "notes": ""},
        )
        entry = AuditLog.objects.get(action="updated", entity_type="Customer")
        self.assertEqual(entry.changes["name"], ["Ali", "Ali Khan"])

    def test_login_is_audited(self):
        self.assertEqual(self.post_login("pass12345").status_code, 302)
        entry = AuditLog.objects.get(action="Login")
        self.assertEqual(entry.actor, self.manager)
        self.assertEqual(entry.entity_label, "mgr1")

    def test_lockout_after_five_failures(self):
        for _ in range(5):
            self.assertEqual(self.post_login("wrong").status_code, 200)
        self.assertEqual(self.post_login("pass12345").status_code, 429)
        self.assertEqual(AuditLog.objects.filter(action="Login failed").count(), 5)

    def test_success_clears_failures(self):
        for _ in range(3):
            self.post_login("wrong")
        self.assertEqual(self.post_login("pass12345").status_code, 302)
        self.client.logout()
        for _ in range(4):
            self.assertEqual(self.post_login("wrong").status_code, 200)

    def test_audit_report_includes_audit_entries(self):
        customer = Customer.objects.create(name="Ali")
        log_audit(None, "created", customer, actor=self.manager)
        columns = audit_report(self.auditor, {})
        sources = {dict(zip(columns.columns, row))["Source"] for row in columns.rows}
        self.assertIn("Customer", sources)

    def test_prune_old_data(self):
        old = timezone.now() - timedelta(days=400)
        log_audit(None, "created", entity_type="Customer", entity_label="Old", actor=self.manager)
        log_audit(None, "created", entity_type="Customer", entity_label="New", actor=self.manager)
        AuditLog.objects.filter(entity_label="Old").update(created_at=old)
        read = Notification.objects.create(
            recipient=self.manager, kind="assignment", message="old", read_at=timezone.now()
        )
        unread = Notification.objects.create(recipient=self.manager, kind="assignment", message="unread")
        Notification.objects.filter(pk__in=[read.pk, unread.pk]).update(created_at=old)

        call_command("prune_old_data", verbosity=0)

        labels = set(AuditLog.objects.values_list("entity_label", flat=True))
        self.assertNotIn("Old", labels)
        self.assertIn("New", labels)
        self.assertFalse(Notification.objects.filter(pk=read.pk).exists())
        self.assertTrue(Notification.objects.filter(pk=unread.pk).exists())