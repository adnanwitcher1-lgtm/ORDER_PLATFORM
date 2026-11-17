from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from accounts.models import User
from customers.models import Customer
from orders.models import MetricObservation, Order, OrderEvent
from orders.observations import record_observation
from orders.workflow import apply_transition, assign_order
from ytchannels.models import Channel

DEMO_TAG = "[demo]"
DEMO_PASSWORD = "Demo@12345"

# (customer, channel name, handle or full URL, baseline, increase, priority, stage, observed count)
SCENARIOS = [
    ("saeed", "Saeed Main", "saeed_demo_main", 500, 100, "normal", "draft", None),
    ("saeed", "Saeed Gaming", "saeed_demo_gaming", 1200, 300, "high", "pending", None),
    ("saeed", "Saeed Vlogs", "saeed_demo_vlogs", 800, 200, "normal", "approved", None),
    ("saeed", "Saeed Shorts", "saeed_demo_shorts", 300, 50, "low", "assigned", None),
    ("saeed", "Saeed Music", "saeed_demo_music", 500, 100, "normal", "monitoring", 560),
    ("saeed", "Saeed Cooking", "saeed_demo_cooking", 2000, 500, "high", "monitoring", 2500),
    ("saeed", "Saeed API Test", "https://www.youtube.com/@YouTube", 1000, 500, "normal", "monitoring", 1000),
    ("saeed", "Saeed Old", "saeed_demo_old", 250, 50, "low", "cancelled", None),
    ("bilal", "Bilal Tech", "bilal_demo_tech", 100, 100, "normal", "monitoring", 125),
    ("bilal", "Bilal News", "bilal_demo_news", 900, 100, "normal", "paused", 930),
    ("bilal", "Bilal Reviews", "bilal_demo_reviews", 400, 100, "normal", "completed", 500),
]


class Command(BaseCommand):
    help = "Creates demo data for development (customers Saeed and Bilal, 11 orders, 4 users)."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Delete old demo orders and recreate them.")

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("This command only runs in development (DEBUG=True).")

        if options["reset"]:
            count, _ = Order.objects.filter(notes__startswith=DEMO_TAG).delete()
            self.stdout.write(f"Old demo data removed ({count} records).")
        elif Order.objects.filter(notes__startswith=DEMO_TAG).exists():
            self.stdout.write("Demo data already exists. To recreate it run: python manage.py seed_demo --reset")
            return

        with transaction.atomic():
            self.seed()

    def get_user(self, username, role):
        user = User.objects.filter(username=username).first()
        if user:
            self.stdout.write(f"  {username}: already exists (password unchanged)")
            return user
        self.stdout.write(f"  {username}: created (password: {DEMO_PASSWORD})")
        return User.objects.create_user(username, password=DEMO_PASSWORD, role=role)

    def seed(self):
        self.stdout.write("Users:")
        self.sales = self.get_user("sales1", User.Role.SALES)
        self.manager = self.get_user("mgr1", User.Role.MANAGER)
        self.op1 = self.get_user("op1", User.Role.OPERATOR)
        self.get_user("op2", User.Role.OPERATOR)

        customers = {}
        for key, name, phone in (("saeed", "Saeed", "0300-1111111"), ("bilal", "Bilal", "0300-2222222")):
            customers[key], _ = Customer.objects.get_or_create(
                name=name,
                defaults={"phone": phone, "notes": f"{DEMO_TAG} demo customer", "created_by": self.sales},
            )

        for key, channel_name, handle, baseline, increase, priority, stage, observed in SCENARIOS:
            url = handle if handle.startswith("http") else f"https://www.youtube.com/@{handle}"
            channel, _ = Channel.objects.get_or_create(
                url=url,
                defaults={"customer": customers[key], "name": f"{channel_name} (demo)", "created_by": self.sales},
            )
            order = Order.objects.create(
                customer=channel.customer, channel=channel,
                baseline_count=baseline, requested_increase=increase, priority=priority,
                notes=f"{DEMO_TAG} {channel_name}", created_by=self.sales,
            )
            OrderEvent.objects.create(
                order=order, actor=self.sales, action="Order created", to_status=order.status,
                note=f"Baseline {baseline}, increase {increase}, target {order.target_count}",
            )
            self.advance(order, stage, observed)

        self.stdout.write(self.style.SUCCESS(
            f"\nDemo data ready: {len(SCENARIOS)} orders. Login: sales1 / mgr1 / op1 / op2 (password {DEMO_PASSWORD})"
        ))
        self.stdout.write("This password is for the local demo only. Delete these users from the admin before real use.")

    def advance(self, order, stage, observed):
        if stage == "cancelled":
            apply_transition(order, "cancel_draft", self.sales, "Demo: wrong entry")
            return
        if stage == "draft":
            return

        apply_transition(order, "submit", self.sales)
        if stage == "pending":
            return

        apply_transition(order, "approve", self.manager)
        if stage == "approved":
            return

        assign_order(order, self.op1, self.manager, "Demo assignment")
        if stage == "assigned":
            return

        apply_transition(order, "start_monitoring", self.op1)
        if observed is not None:
            self.add_history(order, observed)
            record_observation(
                order, count=observed, accuracy=MetricObservation.Accuracy.EXACT,
                source=MetricObservation.Source.MANUAL, user=self.op1, note="Demo manual check",
            )
        if stage == "paused":
            apply_transition(order, "pause", self.manager, "Demo: customer asked to stop")
        elif stage == "completed":
            apply_transition(order, "complete", self.manager, "Demo: source verified")

    def add_history(self, order, observed):
        """Back-dated earlier counts, so the growth chart and the estimate have a trend to show."""
        now = timezone.now()
        steps = (5, 4, 3, 2, 1)
        gain = observed - order.baseline_count
        for i, days_ago in enumerate(steps, start=1):
            share = i / (len(steps) + 1)
            MetricObservation.objects.create(
                order=order, observed_count=order.baseline_count + int(gain * share * 0.9),
                accuracy=MetricObservation.Accuracy.EXACT, source=MetricObservation.Source.MANUAL,
                observed_at=now - timedelta(days=days_ago), note="Demo history",
            )
