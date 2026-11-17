import time

from django.core.management.base import BaseCommand
from django.utils import timezone

from orders.anomalies import notify_anomalies
from orders.monitoring import notify_overdue_reviews, run_due_checks


class Command(BaseCommand):
    help = "Runs YouTube checks for due orders (Celery/Redis not required)."

    def add_arguments(self, parser):
        parser.add_argument("--loop", action="store_true", help="Run repeatedly (stop with Ctrl+C).")
        parser.add_argument("--every", type=int, default=300, help="Seconds between runs in loop mode (default 300).")

    def handle(self, *args, **options):
        try:
            while True:
                summary = run_due_checks()
                overdue = notify_overdue_reviews()
                stalled = notify_anomalies()
                self.stdout.write(
                    f"{timezone.localtime():%H:%M:%S}  {summary}  overdue_notified={overdue}  stalled_alerts={stalled}"
                )
                if not options["loop"]:
                    break
                time.sleep(options["every"])
        except KeyboardInterrupt:
            self.stdout.write("Stopped.")