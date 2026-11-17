import unittest

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from customers.models import Customer
from googleaccounts.models import ConnectionEvent, GoogleAccountConnection
from orders.models import Order, OrderEvent
from ytchannels.models import Channel

from .builders import audit_report, daily_operations, order_progress, staff_workload
from .exports import safe_cell

try:
    import reportlab  # noqa: F401
    HAS_REPORTLAB = True
except ImportError:
    HAS_REPORTLAB = False


def as_dicts(data):
    return [dict(zip(data.columns, row)) for row in data.rows]


class ReportBase(TestCase):
    def setUp(self):
        User = get_user_model()
        self.manager = User.objects.create_user("mgr1", password="pass12345", role="manager")
        self.sales = User.objects.create_user("sales1", password="pass12345", role="sales")
        self.op1 = User.objects.create_user("op1", password="pass12345", role="operator")
        self.op2 = User.objects.create_user("op2", password="pass12345", role="operator")
        self.auditor = User.objects.create_user("aud1", password="pass12345", role="auditor")
        self.ali = Customer.objects.create(name="Ali")
        self.bilal = Customer.objects.create(name="Bilal")
        self.ali_ch = Channel.objects.create(customer=self.ali, name="Ali TV", url="https://www.youtube.com/@alitv")
        self.bilal_ch = Channel.objects.create(customer=self.bilal, name="Bilal TV", url="https://www.youtube.com/@bilaltv")

    def make_order(self, customer=None, channel=None, **kwargs):
        data = dict(
            customer=customer or self.ali, channel=channel or self.ali_ch,
            requested_increase=100, baseline_count=500,
        )
        data.update(kwargs)
        return Order.objects.create(**data)


class BuilderTests(ReportBase):
    def test_order_progress_values(self):
        self.make_order(status=Order.Status.MONITORING, assigned_to=self.op1,
                        latest_observed_count=560, latest_observed_accuracy="exact")
        row = as_dicts(order_progress(self.manager, {}))[0]
        self.assertEqual(row["Target"], 600)
        self.assertEqual(row["Observed change"], 60)
        self.assertEqual(row["Remaining gap"], 40)
        self.assertEqual(row["Progress"], "60%")
        self.assertEqual(row["Type"], "Exact")

    def test_order_progress_no_data(self):
        self.make_order(status=Order.Status.MONITORING, assigned_to=self.op1)
        row = as_dicts(order_progress(self.manager, {}))[0]
        self.assertEqual(row["Latest"], "N/A")
        self.assertEqual(row["Progress"], "No data")

    def test_operator_sees_only_own_orders(self):
        self.make_order(status=Order.Status.MONITORING, assigned_to=self.op1)
        self.make_order(status=Order.Status.MONITORING, assigned_to=self.op2)
        self.assertEqual(len(order_progress(self.op1, {}).rows), 1)
        self.assertEqual(len(order_progress(self.manager, {}).rows), 2)

    def test_staff_workload_counts(self):
        for status in (Order.Status.MONITORING, Order.Status.MONITORING,
                       Order.Status.TARGET_OBSERVED, Order.Status.EXCEPTION):
            self.make_order(status=status, assigned_to=self.op1)
        self.make_order(status=Order.Status.COMPLETED, assigned_to=self.op1, closed_at=timezone.now())

        row = next(r for r in as_dicts(staff_workload(self.manager, {})) if r["Staff"] == "op1")
        self.assertEqual(row["Open orders"], 4)
        self.assertEqual(row["Monitoring"], 2)
        self.assertEqual(row["Pending review"], 1)
        self.assertEqual(row["Exceptions"], 1)
        self.assertEqual(row["Completed"], 1)

    def test_daily_operations_counts(self):
        first = self.make_order()
        self.make_order()
        OrderEvent.objects.create(order=first, action="Target observed")
        today = as_dicts(daily_operations(self.manager, {}))[0]
        self.assertEqual(today["New orders"], 2)
        self.assertEqual(today["Target observed"], 1)

    def test_audit_merges_sources(self):
        order = self.make_order()
        OrderEvent.objects.create(order=order, actor=self.manager, action="Approved")
        connection = GoogleAccountConnection.objects.create(label="Main", google_sub="sub-1")
        ConnectionEvent.objects.create(connection=connection, actor=self.manager, action="Connected")
        rows = as_dicts(audit_report(self.auditor, {}))
        self.assertEqual({r["Source"] for r in rows}, {"Order", "Google account"})


class CsvSafetyTests(SimpleTestCase):
    def test_safe_cell(self):
        self.assertTrue(safe_cell("=cmd|' /C calc'!A0").startswith("'"))
        self.assertTrue(safe_cell("@SUM(1)").startswith("'"))
        self.assertEqual(safe_cell(-20), -20)
        self.assertEqual(safe_cell(None), "")
        self.assertEqual(safe_cell("Ali"), "Ali")


class ReportViewTests(ReportBase):
    def test_sales_can_open_customer_history(self):
        self.client.force_login(self.sales)
        self.assertEqual(self.client.get(reverse("reports:detail", args=["customer_history"])).status_code, 200)

    def test_sales_cannot_open_order_progress(self):
        self.client.force_login(self.sales)
        self.assertEqual(self.client.get(reverse("reports:detail", args=["order_progress"])).status_code, 403)

    def test_operator_cannot_open_audit(self):
        self.client.force_login(self.op1)
        self.assertEqual(self.client.get(reverse("reports:detail", args=["audit"])).status_code, 403)

    def test_auditor_can_open_audit(self):
        self.client.force_login(self.auditor)
        self.assertEqual(self.client.get(reverse("reports:detail", args=["audit"])).status_code, 200)

    def test_csv_export_respects_filter(self):
        self.make_order(customer=self.ali, channel=self.ali_ch)
        self.make_order(customer=self.bilal, channel=self.bilal_ch)
        self.client.force_login(self.manager)
        url = reverse("reports:export", args=["customer_history", "csv"])
        response = self.client.get(url, {"customer": self.ali.pk})
        text = response.content.decode("utf-8-sig")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response["Content-Type"])
        self.assertIn("Ali TV", text)
        self.assertNotIn("Bilal TV", text)

    def test_invalid_date_redirects_back(self):
        self.client.force_login(self.manager)
        url = reverse("reports:export", args=["customer_history", "csv"])
        response = self.client.get(url, {"date_from": "not-a-date"})
        self.assertEqual(response.status_code, 302)

    @unittest.skipUnless(HAS_REPORTLAB, "reportlab is not installed")
    def test_pdf_export(self):
        self.make_order()
        self.client.force_login(self.manager)
        response = self.client.get(reverse("reports:export", args=["customer_history", "pdf"]))
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))

    def test_index_lists_only_allowed_reports(self):
        self.client.force_login(self.sales)
        text = self.client.get(reverse("reports:index")).content.decode()
        self.assertIn("Customer History", text)
        self.assertNotIn("Audit Report", text)