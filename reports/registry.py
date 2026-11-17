from dataclasses import dataclass
from typing import Callable, Optional

from accounts.models import User
from googleaccounts.models import GoogleAccountConnection
from orders.models import Order

from . import builders

R = User.Role


@dataclass(frozen=True)
class ReportDef:
    key: str
    title: str
    description: str
    roles: tuple
    filters: tuple
    build: Callable
    status_choices: Optional[Callable] = None
    default_days: int = 0


REPORTS = [
    ReportDef(
        "daily_operations", "Daily Operations",
        "New, approved, target observed, completed, cancelled and exception orders per day.",
        (R.MANAGER, R.AUDITOR), ("date_from", "date_to"),
        builders.daily_operations, default_days=7,
    ),
    ReportDef(
        "customer_history", "Customer History",
        "Orders, channels, quantities and status for each customer.",
        (R.MANAGER, R.SALES, R.AUDITOR), ("date_from", "date_to", "customer", "status"),
        builders.customer_history, status_choices=lambda: Order.Status.choices,
    ),
    ReportDef(
        "order_progress", "Order Progress",
        "Baseline, target, latest count, observed change, remaining gap and progress.",
        (R.MANAGER, R.OPERATOR, R.AUDITOR), ("date_from", "date_to", "customer", "status", "staff"),
        builders.order_progress, status_choices=lambda: Order.Status.choices,
    ),
    ReportDef(
        "staff_workload", "Staff Workload",
        "Open, pending review, overdue and completed orders for each staff member.",
        (R.MANAGER, R.AUDITOR), ("date_from", "date_to", "staff"),
        builders.staff_workload,
    ),
    ReportDef(
        "account_health", "Account Health",
        "Google account connection status, last successful check and errors.",
        (R.MANAGER, R.OPERATOR, R.AUDITOR), ("status",),
        builders.account_health, status_choices=lambda: GoogleAccountConnection.Status.choices,
    ),
    ReportDef(
        "audit", "Audit Report",
        "Every important action on orders and Google accounts: who, when, what.",
        (R.AUDITOR,), ("date_from", "date_to", "staff"),
        builders.audit_report, default_days=7,
    ),
]

REPORT_MAP = {r.key: r for r in REPORTS}


def can_view(user, definition):
    return user.is_superuser or user.role in definition.roles


def visible_reports(user):
    return [r for r in REPORTS if can_view(user, r)]


def visible_filters(user, definition):
    filters = definition.filters
    # Operators only see their own orders, so the staff filter is hidden.
    if not user.is_superuser and user.role == R.OPERATOR:
        filters = tuple(name for name in filters if name != "staff")
    return filters