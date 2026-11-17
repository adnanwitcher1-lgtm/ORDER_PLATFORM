from celery import shared_task

from .anomalies import notify_anomalies
from .monitoring import notify_overdue_reviews, run_due_checks


@shared_task(name="orders.tasks.check_due_orders")
def check_due_orders():
    summary = run_due_checks()
    summary["overdue_notified"] = notify_overdue_reviews()
    summary["stalled_alerts"] = notify_anomalies()
    return summary