from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from customers.models import Customer
from reports.client_report import has_reportable_orders
from reports import whatsapp
from reports.delivery import DeliveryError, send_customer_report

MIN_DAYS_BETWEEN = 6   # so a daily cron job still sends only once a week


class Command(BaseCommand):
    help = "Sends the weekly report: PDF by email and/or a WhatsApp message, for customers who have it enabled."

    def add_arguments(self, parser):
        parser.add_argument("--customer", type=int, help="Send only to this customer ID (ignores the weekly rule).")
        parser.add_argument("--dry-run", action="store_true", help="Show who would receive a report.")
        parser.add_argument("--force", action="store_true", help="Ignore the 'sent in the last 6 days' rule.")

    def handle(self, *args, **options):
        base = Customer.objects.filter(status=Customer.Status.ACTIVE)
        if options["customer"]:
            base = base.filter(pk=options["customer"])
        cutoff = timezone.now() - timedelta(days=MIN_DAYS_BETWEEN)
        self.counts = {"sent": 0, "skipped": 0, "failed": 0}
        explicit = bool(options["customer"])

        emails = base.exclude(email="") if explicit else base.exclude(email="").filter(weekly_report_enabled=True)
        for customer in emails:
            self.run(customer, "email", customer.last_report_sent_at, cutoff, options, explicit,
                     lambda c: send_customer_report(c), DeliveryError, f"<{customer.email}>")

        if explicit and not whatsapp.is_configured():
            self.stdout.write("WhatsApp API is not configured: skipped WhatsApp.")
        elif whatsapp.is_configured():
            chats = base if explicit else base.filter(weekly_whatsapp_enabled=True)
            for customer in chats:
                if not customer.whatsapp_number():
                    continue
                self.run(customer, "WhatsApp", customer.last_whatsapp_sent_at, cutoff, options, explicit,
                         lambda c: whatsapp.send_whatsapp_report(c), whatsapp.WhatsAppError,
                         customer.whatsapp_number())
        c = self.counts
        self.stdout.write(f"Done. sent={c['sent']} skipped={c['skipped']} failed={c['failed']}")

    def run(self, customer, channel, last_sent, cutoff, options, explicit, action, error_class, target):
        if not explicit and not options["force"] and last_sent and last_sent > cutoff:
            self.counts["skipped"] += 1
            return
        if not has_reportable_orders(customer):
            self.counts["skipped"] += 1
            return
        if options["dry_run"]:
            self.stdout.write(f"Would send {channel} to {customer.name} {target}")
            self.counts["sent"] += 1
            return
        try:
            action(customer)
        except error_class as exc:
            self.counts["failed"] += 1
            self.stderr.write(f"{customer.name} ({channel}): {exc}")
        else:
            self.counts["sent"] += 1
            self.stdout.write(f"Sent {channel} to {customer.name} {target}")
