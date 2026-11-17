from dataclasses import dataclass
from typing import Optional
from django.urls import reverse

from notifications.models import Notification
from notifications.services import notify
from django.db import transaction
from django.utils import timezone

from accounts.models import User

from .models import Order, OrderAssignmentHistory, OrderEvent

S = Order.Status
R = User.Role

CLOSED_STATUSES = (S.COMPLETED, S.CANCELLED)
ASSIGNABLE_STATUSES = (S.APPROVED, S.ASSIGNED, S.MONITORING, S.PAUSED, S.EXCEPTION, S.TARGET_OBSERVED)
ASSIGN_ROLES = (R.MANAGER,)
ASSIGNEE_ROLES = (R.OPERATOR, R.MANAGER)

EDIT = (R.MANAGER, R.SALES)
MGR = (R.MANAGER,)


class WorkflowError(Exception):
    """Error message that is safe to show to the user."""


@dataclass(frozen=True)
class Transition:
    key: str
    label: str            # on the button
    done: str             # in the activity log
    sources: tuple        # statuses from which this action is allowed
    target: Optional[str]  # None = dynamic (resume)
    roles: tuple
    needs_reason: bool = False
    ask_note: bool = False
    assignee_only: tuple = ()   # these roles may act only on their own assigned orders
    style: str = "primary"


TRANSITIONS = {
    t.key: t
    for t in [
        Transition("submit", "Submit for Review", "Submitted for review",
                   (S.DRAFT,), S.PENDING_REVIEW, EDIT),
        Transition("approve", "Approve", "Approved",
                   (S.PENDING_REVIEW,), S.APPROVED, MGR, style="success"),
        Transition("return_to_draft", "Return to Draft", "Returned to draft",
                   (S.PENDING_REVIEW,), S.DRAFT, MGR, needs_reason=True, style="warning"),
        Transition("start_monitoring", "Start Monitoring", "Monitoring started",
                   (S.ASSIGNED,), S.MONITORING, (R.MANAGER, R.OPERATOR),
                   assignee_only=(R.OPERATOR,)),
        Transition("pause", "Pause", "Paused",
                   (S.APPROVED, S.ASSIGNED, S.MONITORING), S.PAUSED, MGR,
                   needs_reason=True, style="warning"),
        Transition("resume", "Resume", "Resumed",
                   (S.PAUSED, S.EXCEPTION), None, MGR, style="success"),
        Transition("record_exception", "Record Exception", "Exception recorded",
                   (S.ASSIGNED, S.MONITORING, S.TARGET_OBSERVED), S.EXCEPTION,
                   (R.MANAGER, R.OPERATOR), needs_reason=True,
                   assignee_only=(R.OPERATOR,), style="warning"),
        Transition("reopen", "Reopen Monitoring", "Reopened for monitoring",
                   (S.TARGET_OBSERVED,), S.MONITORING, MGR, needs_reason=True, style="warning"),
        Transition("complete", "Complete Order", "Completed after review",
                   (S.TARGET_OBSERVED,), S.COMPLETED, MGR, ask_note=True, style="success"),
        Transition("cancel_draft", "Cancel Order", "Cancelled",
                   (S.DRAFT, S.PENDING_REVIEW), S.CANCELLED, EDIT,
                   needs_reason=True, style="danger"),
        Transition("cancel", "Cancel Order", "Cancelled",
                   (S.APPROVED, S.ASSIGNED, S.MONITORING, S.PAUSED, S.TARGET_OBSERVED, S.EXCEPTION),
                   S.CANCELLED, MGR, needs_reason=True, style="danger"),
    ]
}


def can_transition(user, order, transition):
    if order.status not in transition.sources:
        return False
    if user.is_superuser:
        return True
    if user.role not in transition.roles:
        return False
    if user.role in transition.assignee_only and order.assigned_to_id != user.id:
        return False
    return True


def available_transitions(user, order):
    return [t for t in TRANSITIONS.values() if can_transition(user, order, t)]


def can_assign(user, order):
    return (user.is_superuser or user.role in ASSIGN_ROLES) and order.status in ASSIGNABLE_STATUSES


def resume_target(order):
    """Return to the status before pause/exception, otherwise monitoring/approved."""
    last = order.events.filter(to_status=order.status).first()
    if last and last.from_status and last.from_status not in (S.PAUSED, S.EXCEPTION):
        return last.from_status
    return S.MONITORING if order.assigned_to_id else S.APPROVED


@transaction.atomic
def apply_transition(order, key, user, reason=""):
    transition = TRANSITIONS.get(key)
    if transition is None:
        raise WorkflowError("This action does not exist.")

    order = Order.objects.select_for_update().get(pk=order.pk)
    if not can_transition(user, order, transition):
        raise WorkflowError(
            "You cannot perform this action, or the order status no longer allows it."
        )

    reason = (reason or "").strip()
    if transition.needs_reason and not reason:
        raise WorkflowError("A reason is required for this action.")

    old_status = str(order.status)
    new_status = str(transition.target or resume_target(order))
    now = timezone.now()

    order.status = new_status
    fields = ["status", "updated_at"]
    if key == "approve":
        order.approved_at = now
        fields.append("approved_at")
    if new_status in CLOSED_STATUSES:
        order.closed_at = now
        fields.append("closed_at")
    if key == "complete":
        order.reviewed_by = user
        order.reviewed_at = now
        fields += ["reviewed_by", "reviewed_at"]
    order.save(update_fields=fields)

    OrderEvent.objects.create(
        order=order, actor=user, action=transition.done,
        from_status=old_status, to_status=new_status, note=reason,
    )
    order.refresh_from_db()
    return order


@transaction.atomic
def assign_order(order, assignee, user, reason=""):
    order = Order.objects.select_for_update().get(pk=order.pk)
    if not can_assign(user, order):
        raise WorkflowError("You cannot assign this order, or the order cannot be assigned right now.")
    if not assignee.is_active or assignee.role not in ASSIGNEE_ROLES:
        raise WorkflowError("This user cannot be assigned orders.")
    if order.assigned_to_id == assignee.pk:
        raise WorkflowError("The order is already assigned to this user.")

    reason = (reason or "").strip()
    previous = order.assigned_to
    old_status = str(order.status)
    new_status = str(S.ASSIGNED) if old_status == S.APPROVED else old_status

    order.assigned_to = assignee
    order.status = new_status
    order.save(update_fields=["assigned_to", "status", "updated_at"])

    OrderAssignmentHistory.objects.create(
        order=order, assigned_to=assignee, changed_by=user, reason=reason
    )

    if previous:
        action = "Reassigned"
        note = f"{previous.display_name} -> {assignee.display_name}"
    else:
        action = "Assigned"
        note = assignee.display_name
    if reason:
        note += f" ({reason})"

    OrderEvent.objects.create(
        order=order, actor=user, action=action,
        from_status=old_status, to_status=new_status, note=note,
    )
    order.refresh_from_db()
    notify(
        [assignee], Notification.Kind.ASSIGNMENT,
        f"{order.order_number} was assigned to you ({order.customer.name}).",
        link=reverse("orders:detail", args=[order.pk]), exclude=user,
    )
    return order