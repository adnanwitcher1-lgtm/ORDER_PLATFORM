from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from customers.models import Customer
from orders.models import Order
from orders.workflow import assign_order
from ytchannels.models import Channel

from .models import Notification
from .services import notify

K = Notification.Kind


class NotificationTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.manager = User.objects.create_user("mgr1", password="pass12345", role="manager")
        self.op1 = User.objects.create_user("op1", password="pass12345", role="operator")
        self.op2 = User.objects.create_user("op2", password="pass12345", role="operator")

    def test_assignment_notifies_assignee_only(self):
        customer = Customer.objects.create(name="Ali")
        channel = Channel.objects.create(customer=customer, name="Ali TV", url="https://www.youtube.com/@alitv")
        order = Order.objects.create(
            customer=customer, channel=channel, requested_increase=100,
            baseline_count=500, status=Order.Status.APPROVED,
        )
        assign_order(order, self.op1, self.manager)
        self.assertEqual(Notification.objects.filter(recipient=self.op1, kind=K.ASSIGNMENT).count(), 1)
        self.assertEqual(Notification.objects.filter(recipient=self.manager).count(), 0)

    def test_dedupe_until_read(self):
        notify([self.op1], K.API_ERROR, "quota", dedupe=True)
        notify([self.op1], K.API_ERROR, "quota", dedupe=True)
        self.assertEqual(Notification.objects.filter(recipient=self.op1).count(), 1)
        Notification.objects.update(read_at=timezone.now())
        notify([self.op1], K.API_ERROR, "quota", dedupe=True)
        self.assertEqual(Notification.objects.filter(recipient=self.op1).count(), 2)

    def test_open_marks_read_and_redirects(self):
        item = Notification.objects.create(recipient=self.op1, kind=K.ASSIGNMENT, message="x", link="/orders/")
        self.client.force_login(self.op1)
        response = self.client.get(reverse("notifications:open", args=[item.pk]))
        item.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/orders/")
        self.assertTrue(item.is_read)

    def test_cannot_open_other_users_notification(self):
        item = Notification.objects.create(recipient=self.op1, kind=K.ASSIGNMENT, message="x")
        self.client.force_login(self.op2)
        self.assertEqual(self.client.get(reverse("notifications:open", args=[item.pk])).status_code, 404)

    def test_mark_all_read(self):
        Notification.objects.create(recipient=self.op1, kind=K.ASSIGNMENT, message="a")
        Notification.objects.create(recipient=self.op1, kind=K.ASSIGNMENT, message="b")
        self.client.force_login(self.op1)
        self.client.post(reverse("notifications:read_all"))
        self.assertEqual(Notification.objects.filter(recipient=self.op1, read_at__isnull=True).count(), 0)