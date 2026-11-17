from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from notifications.models import Notification
from notifications.services import managers_and_admins, notify

from .anomalies import check_drop
from .models import MetricObservation, Order, OrderEvent

S = Order.Status
A = MetricObservation.Accuracy

OBSERVABLE_STATUSES = (S.ASSIGNED, S.MONITORING, S.EXCEPTION, S.TARGET_OBSERVED)
FETCHABLE_STATUSES = (S.ASSIGNED, S.MONITORING, S.EXCEPTION)   # polling stops after target observed
AUTO_REVIEW_FROM = (S.ASSIGNED, S.MONITORING)


class ObservationError(Exception):
    """Error that is safe to show to the user."""


def _allowed(user, order, statuses):
    if order.status not in statuses:
        return False
    if user.is_superuser or user.role == User.Role.MANAGER:
        return True
    return user.role == User.Role.OPERATOR and order.assigned_to_id == user.id


def can_record_observation(user, order):
    return _allowed(user, order, OBSERVABLE_STATUSES)


def can_fetch_observation(user, order):
    return _allowed(user, order, FETCHABLE_STATUSES)


@transaction.atomic
def record_observation(order, *, count, accuracy, source, user=None, note="", api_status=""):
    order = Order.objects.select_for_update().get(pk=order.pk)
    if order.status not in OBSERVABLE_STATUSES:
        raise ObservationError("An observation cannot be recorded in this status.")

    valid = accuracy != A.UNAVAILABLE
    if valid and count is None:
        raise ObservationError("A count is required.")

    now = timezone.now()
    observation = MetricObservation.objects.create(
        order=order,
        observed_count=count if valid else None,
        observed_at=now,
        source=source,
        accuracy=accuracy,
        api_status=api_status,
        note=note,
        recorded_by=user,
    )

    if valid:
        previous = order.latest_observed_count
        order.latest_observed_count = count
        order.latest_observed_at = now
        order.latest_observed_accuracy = accuracy
        fields = ["latest_observed_count", "latest_observed_at", "latest_observed_accuracy", "updated_at"]

        # When the target is observed the order is only flagged for review; it is never completed automatically.
        target_hit = count >= order.target_count and order.status in AUTO_REVIEW_FROM
        if target_hit:
            old_status = str(order.status)
            order.status = S.TARGET_OBSERVED
            fields.append("status")
            OrderEvent.objects.create(
                order=order, actor=user, action="Target observed",
                from_status=old_status, to_status=str(order.status),
                note=f"Observed {count} ({accuracy}), target {order.target_count}. Review required.",
            )
        order.save(update_fields=fields)
        check_drop(order, previous, count, now)

        if target_hit:
            recipients = managers_and_admins()
            if order.assigned_to_id:
                recipients.append(order.assigned_to)
            notify(
                recipients, Notification.Kind.TARGET_OBSERVED,
                f"{order.order_number}: target observed, review required.",
                link=reverse("orders:detail", args=[order.pk]), exclude=user,
            )
    else:
        OrderEvent.objects.create(
            order=order, actor=user, action="Metric unavailable",
            from_status=str(order.status), to_status=str(order.status),
            note=note or api_status,
        )

    return observation, order