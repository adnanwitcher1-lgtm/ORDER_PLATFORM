from datetime import timedelta

from django.conf import settings
from django.db.models import Max, Q
from django.urls import reverse
from django.utils import timezone
from core.models import SiteSettings
from notifications.models import Notification
from notifications.services import managers_and_admins, notify

from .models import MetricObservation, Order, OrderEvent
from .observations import ObservationError, record_observation
from .youtube import fetch_many, fetch_subscriber_count

S = Order.Status
A = MetricObservation.Accuracy
POLLED_STATUSES = (S.ASSIGNED, S.MONITORING)
CLAIM_MINUTES = 10                                 # a second run must not pick the same order during this time
RECORD_FAILURE_STATUSES = ("hidden", "not_found", "error")


def _interval():
    return timedelta(minutes=SiteSettings.load().monitoring_interval_minutes)



def _backoff(failures):
    """5, 10, 20, 40 minutes... but never more than the check interval."""
    cfg = SiteSettings.load()
    minutes = cfg.retry_base_minutes * 2 ** (failures - 1)
    return timedelta(minutes=min(minutes, cfg.monitoring_interval_minutes))


def _link(order):
    return reverse("orders:detail", args=[order.pk])


def _recipients(order):
    people = managers_and_admins()
    if order.assigned_to_id:
        people.append(order.assigned_to)
    return people


def due_orders(now):
    return Order.objects.filter(status__in=POLLED_STATUSES).filter(
        Q(next_check_at__isnull=True) | Q(next_check_at__lte=now)
    ).select_related("channel", "assigned_to")


def _fetch_for_channels(orders):
    """{channel_pk: FetchResult}. channels with an ID go in a batch, the rest one by one."""
    channels = {o.channel_id: o.channel for o in orders}
    by_id = {}
    for channel in channels.values():
        if channel.youtube_channel_id:
            by_id.setdefault(channel.youtube_channel_id, []).append(channel.pk)

    results = {}
    batch = fetch_many(list(by_id)) if by_id else {}
    for youtube_id, pks in by_id.items():
        for pk in pks:
            results[pk] = batch[youtube_id]

    for pk, channel in channels.items():
        if pk in results:
            continue
        result = fetch_subscriber_count(channel)
        results[pk] = result
        # Save the ID resolved from the handle so the next check can use a batch.
        if result.channel_id and not channel.youtube_channel_id:
            channel.youtube_channel_id = result.channel_id
            channel.save(update_fields=["youtube_channel_id", "updated_at"])
    return results


def _handle_success(order, result, now):
    try:
        record_observation(
            order, count=result.count, accuracy=result.accuracy,
            source=MetricObservation.Source.YOUTUBE_API, user=None,
            note=result.message, api_status="ok",
        )
    except ObservationError:
        return False   # status changed in the meantime (e.g. paused), skip it
    Order.objects.filter(pk=order.pk).update(check_failures=0, next_check_at=now + _interval())
    return True


def _handle_failure(order, result, now):
    """Returns True if the order moved to Exception."""
    failures = order.check_failures + 1

    if result.api_status in RECORD_FAILURE_STATUSES:
        try:
            record_observation(
                order, count=None, accuracy=A.UNAVAILABLE,
                source=MetricObservation.Source.YOUTUBE_API, user=None,
                note=result.message, api_status=result.api_status,
            )
        except ObservationError:
            return False

    if failures >= SiteSettings.load().max_failures:
        changed = Order.objects.filter(pk=order.pk, status__in=POLLED_STATUSES).update(
            status=S.EXCEPTION, check_failures=0, next_check_at=None, updated_at=now
        )
        if not changed:
            return False
        OrderEvent.objects.create(
            order=order, actor=None, action="Auto-check stopped",
            from_status=str(order.status), to_status=str(S.EXCEPTION),
            note=f"{failures} consecutive check failures: {result.message}",
        )
        notify(
            _recipients(order), Notification.Kind.EXCEPTION,
            f"{order.order_number}: {failures} consecutive check failures, order moved to Exception.",
            link=_link(order),
        )
        return True

    Order.objects.filter(pk=order.pk).update(
        check_failures=failures, next_check_at=now + _backoff(failures)
    )
    return False


def run_due_checks(now=None):
    now = now or timezone.now()
    summary = {"due": 0, "ok": 0, "failed": 0, "exceptions": 0, "stopped": ""}

    orders = list(due_orders(now))
    summary["due"] = len(orders)
    if not orders:
        return summary
    if not getattr(settings, "YOUTUBE_API_KEY", ""):
        summary["stopped"] = "not_configured"
        return summary

    Order.objects.filter(pk__in=[o.pk for o in orders]).update(
        next_check_at=now + timedelta(minutes=CLAIM_MINUTES)
    )
    results = _fetch_for_channels(orders)

    for order in orders:
        result = results[order.channel_id]
        if result.api_status == "quota":
            summary["stopped"] = "quota"
            Order.objects.filter(pk=order.pk).update(next_check_at=now + _interval())
        elif result.api_status == "ok":
            if _handle_success(order, result, now):
                summary["ok"] += 1
        else:
            summary["failed"] += 1
            if _handle_failure(order, result, now):
                summary["exceptions"] += 1

    if summary["stopped"] == "quota":
        notify(
            managers_and_admins(), Notification.Kind.API_ERROR,
            "YouTube API quota is exhausted, scheduled checks were stopped.",
            dedupe=True,
        )
    return summary


def notify_overdue_reviews(now=None):
    now = now or timezone.now()
    hours = SiteSettings.load().review_overdue_hours
    cutoff = now - timedelta(hours=hours)
    orders = (
        Order.objects.filter(status=S.TARGET_OBSERVED)
        .annotate(reached_at=Max("events__created_at", filter=Q(events__action="Target observed")))
        .filter(reached_at__lte=cutoff)
        .select_related("assigned_to")
    )
    sent = 0
    for order in orders:
        sent += notify(
            _recipients(order), Notification.Kind.REVIEW_OVERDUE,
            f"{order.order_number} review has been pending for {hours} hours.",
            link=_link(order), dedupe=True,
        )
    return sent