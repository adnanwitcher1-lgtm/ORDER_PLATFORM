"""Automatic alerts: a sudden drop in the count, or growth that has stopped."""
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from core.models import SiteSettings
from notifications.models import Notification
from notifications.services import managers_and_admins, notify

from .models import MetricObservation, Order, OrderEvent

S = Order.Status
WATCHED = (S.ASSIGNED, S.MONITORING)
SAME_ALERT_HOURS = 24          # the same alert is not repeated within this time
MAX_SCAN = 500


def _recipients(order):
    people = managers_and_admins()
    if order.assigned_to_id:
        people.append(order.assigned_to)
    return people


def _notify_once(order, message, now):
    recent = Notification.objects.filter(
        kind=Notification.Kind.ANOMALY, message=message,
        created_at__gte=now - timedelta(hours=SAME_ALERT_HOURS),
    ).exists()
    if recent:
        return 0
    return notify(
        _recipients(order), Notification.Kind.ANOMALY, message,
        link=reverse("orders:detail", args=[order.pk]),
    )


def check_drop(order, previous, new, now=None):
    """Called when a new count is recorded. Returns True if an alert was raised."""
    if previous is None or new is None or new >= previous or previous <= 0:
        return False
    percent = (previous - new) * 100 / previous
    if percent < SiteSettings.load().drop_alert_percent:
        return False
    now = now or timezone.now()
    message = f"{order.order_number}: count dropped from {previous:,} to {new:,} ({percent:.1f}% lower)."
    OrderEvent.objects.create(
        order=order, actor=None, action="Count dropped",
        from_status=str(order.status), to_status=str(order.status), note=message,
    )
    _notify_once(order, message, now)
    return True


def _count_at(order, moment):
    """The last usable count at or before `moment` (falls back to the baseline)."""
    row = (
        MetricObservation.objects.filter(order=order, observed_at__lte=moment, observed_count__isnull=False)
        .exclude(accuracy=MetricObservation.Accuracy.UNAVAILABLE)
        .order_by("-observed_at", "-pk").values_list("observed_count", flat=True).first()
    )
    if row is not None:
        return row
    return order.baseline_count if order.baseline_recorded_at <= moment else None


def find_stalled(now=None, hours=None):
    """[(order, stuck_count)] for monitored orders with checks coming in but no growth."""
    now = now or timezone.now()
    hours = hours or SiteSettings.load().stalled_alert_hours
    cutoff = now - timedelta(hours=hours)
    orders = (
        Order.objects.filter(status__in=WATCHED, latest_observed_count__isnull=False,
                             latest_observed_at__gte=cutoff)
        .select_related("customer", "channel", "assigned_to")[:MAX_SCAN]
    )
    stalled = []
    for order in orders:
        if order.latest_observed_count >= order.target_count:
            continue
        earlier = _count_at(order, cutoff)
        if earlier is not None and order.latest_observed_count <= earlier:
            stalled.append((order, order.latest_observed_count))
    return stalled


def notify_anomalies(now=None):
    """Raise 'no growth' alerts. Returns the number of orders alerted."""
    now = now or timezone.now()
    hours = SiteSettings.load().stalled_alert_hours
    sent = 0
    for order, count in find_stalled(now, hours):
        message = f"{order.order_number}: no growth for {hours} hours (stuck at {count:,})."
        sent += 1 if _notify_once(order, message, now) else 0
    return sent
