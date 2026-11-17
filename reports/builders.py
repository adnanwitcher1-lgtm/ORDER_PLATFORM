from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta

from django.conf import settings
from django.db.models import Count, Max, Q
from django.utils import timezone
from audit.models import AuditLog
from core.models import SiteSettings
from accounts.models import User
from googleaccounts.models import ConnectionEvent, GoogleAccountConnection
from orders.models import Order, OrderEvent

S = Order.Status
OPEN_STATUSES = (S.APPROVED, S.ASSIGNED, S.MONITORING, S.PAUSED, S.TARGET_OBSERVED, S.EXCEPTION)
MAX_ROWS = 5000
MAX_AUDIT_ROWS = 2000
MAX_DAYS = 92


@dataclass
class ReportData:
    title: str
    columns: list
    rows: list
    summary: list = field(default_factory=list)   # [(label, value), ...]
    note: str = ""


def _dt(value):
    return timezone.localtime(value).strftime("%d %b %Y %H:%M") if value else ""


def _na(value):
    return "N/A" if value is None else value


def _cap(queryset, limit=MAX_ROWS):
    items = list(queryset[: limit + 1])
    truncated = len(items) > limit
    note = f"Only the first {limit} rows are shown; narrow the data with filters." if truncated else ""
    return items[:limit], note


def _filter_orders(qs, f):
    if f.get("customer"):
        qs = qs.filter(customer=f["customer"])
    if f.get("status"):
        qs = qs.filter(status=f["status"])
    if f.get("staff"):
        qs = qs.filter(assigned_to=f["staff"])
    if f.get("date_from"):
        qs = qs.filter(created_at__date__gte=f["date_from"])
    if f.get("date_to"):
        qs = qs.filter(created_at__date__lte=f["date_to"])
    return qs


def _date_range(f, default_days):
    today = timezone.localdate()
    end = f.get("date_to") or today
    start = f.get("date_from") or end - timedelta(days=default_days - 1)
    if start > end:
        start, end = end, start
    note = ""
    if (end - start).days > MAX_DAYS:
        start = end - timedelta(days=MAX_DAYS)
        note = f"The date range is limited to {MAX_DAYS} days."
    return start, end, note


# ---------- 1. Daily operations ----------
def daily_operations(user, f):
    start, end, note = _date_range(f, 7)
    days = defaultdict(lambda: defaultdict(int))

    for created in Order.objects.filter(created_at__date__range=(start, end)).values_list("created_at", flat=True):
        days[timezone.localtime(created).date()]["created"] += 1
    for approved in Order.objects.filter(approved_at__date__range=(start, end)).values_list("approved_at", flat=True):
        days[timezone.localtime(approved).date()]["approved"] += 1

    event_keys = {
        "Target observed": "target",
        "Completed after review": "completed",
        "Cancelled": "cancelled",
        "Exception recorded": "exceptions",
        "Auto-check stopped": "exceptions",
    }
    events = OrderEvent.objects.filter(
        created_at__date__range=(start, end), action__in=list(event_keys)
    ).values_list("action", "created_at")
    for action, created in events:
        days[timezone.localtime(created).date()][event_keys[action]] += 1

    rows, totals = [], defaultdict(int)
    day = end
    while day >= start:
        counts = days.get(day, {})
        values = [counts.get(k, 0) for k in ("created", "approved", "target", "completed", "cancelled", "exceptions")]
        for key, value in zip(("created", "approved", "target", "completed", "cancelled", "exceptions"), values):
            totals[key] += value
        rows.append([day.strftime("%d %b %Y"), *values])
        day -= timedelta(days=1)

    return ReportData(
        title="Daily Operations",
        columns=["Date", "New orders", "Approved", "Target observed", "Completed", "Cancelled", "Exceptions"],
        rows=rows,
        summary=[
            (f"Naye orders ({start:%d %b} - {end:%d %b})", totals["created"]),
            ("Target observed", totals["target"]),
            ("Completed", totals["completed"]),
            ("Currently active orders", Order.objects.filter(status__in=Order.ACTIVE_STATUSES).count()),
            ("Currently awaiting review", Order.objects.filter(status=S.TARGET_OBSERVED).count()),
        ],
        note=note,
    )


# ---------- 2. Customer history ----------
def customer_history(user, f):
    qs = _filter_orders(Order.objects.select_related("customer", "channel"), f)
    orders, note = _cap(qs.order_by("customer__name", "-created_at"))
    rows = [
        [o.customer.name, o.channel.name, o.channel.url, o.order_number, o.baseline_count,
         o.requested_increase, o.target_count, _na(o.latest_observed_count),
         o.get_status_display(), o.created_at.strftime("%d %b %Y") if False else _dt(o.created_at)]
        for o in orders
    ]
    return ReportData(
        title="Customer History",
        columns=["Customer", "Channel", "URL", "Order", "Baseline", "Requested", "Target",
                 "Latest", "Status", "Created"],
        rows=rows,
        summary=[("Orders", len(orders)), ("Customers", len({o.customer_id for o in orders}))],
        note=note,
    )


# ---------- 3. Order progress ----------
def order_progress(user, f):
    qs = Order.objects.select_related("customer", "channel", "assigned_to")
    if not user.is_superuser and user.role == User.Role.OPERATOR:
        qs = qs.filter(assigned_to=user)
    qs = _filter_orders(qs, f)
    orders, note = _cap(qs.order_by("-created_at"))

    rows, reached, no_data = [], 0, 0
    for o in orders:
        p = o.progress
        reached += p.target_reached
        no_data += not p.has_observation
        rows.append([
            o.order_number, o.customer.name, o.channel.name, o.baseline_count, o.target_count,
            _na(o.latest_observed_count),
            o.latest_observed_accuracy.title() if o.latest_observed_accuracy else "",
            p.observed_increase if p.has_observation else "N/A",
            p.remaining_gap if p.has_observation else "N/A",
            f"{p.progress_percent}%" if p.has_observation else "No data",
            _dt(o.latest_observed_at), o.get_status_display(),
            o.assigned_to.display_name if o.assigned_to else "",
        ])
    return ReportData(
        title="Order Progress",
        columns=["Order", "Customer", "Channel", "Baseline", "Target", "Latest", "Type",
                 "Observed change", "Remaining gap", "Progress", "Last checked", "Status", "Assigned to"],
        rows=rows,
        summary=[("Orders", len(orders)), ("Target observed", reached), ("No data", no_data)],
        note=note,
    )


# ---------- 4. Staff workload ----------
def staff_workload(user, f):
    people = User.objects.filter(is_active=True).filter(
        Q(role__in=[User.Role.OPERATOR, User.Role.MANAGER]) | Q(is_superuser=True)
    )
    if f.get("staff"):
        people = people.filter(pk=f["staff"].pk)

    done = Q(assigned_orders__status=S.COMPLETED)
    if f.get("date_from"):
        done &= Q(assigned_orders__closed_at__date__gte=f["date_from"])
    if f.get("date_to"):
        done &= Q(assigned_orders__closed_at__date__lte=f["date_to"])

    people = people.annotate(
        open_total=Count("assigned_orders", filter=Q(assigned_orders__status__in=OPEN_STATUSES), distinct=True),
        monitoring=Count("assigned_orders", filter=Q(assigned_orders__status=S.MONITORING), distinct=True),
        pending=Count("assigned_orders", filter=Q(assigned_orders__status=S.TARGET_OBSERVED), distinct=True),
        exceptions=Count("assigned_orders", filter=Q(assigned_orders__status=S.EXCEPTION), distinct=True),
        completed=Count("assigned_orders", filter=done, distinct=True),
    ).order_by("username")

    cutoff = timezone.now() - timedelta(hours=SiteSettings.load().review_overdue_hours)
    stale = (
        Order.objects.filter(status=S.TARGET_OBSERVED, assigned_to__isnull=False)
        .annotate(reached_at=Max("events__created_at", filter=Q(events__action="Target observed")))
        .filter(reached_at__lte=cutoff)
    )
    overdue = defaultdict(int)
    for assigned_id in stale.values_list("assigned_to_id", flat=True):
        overdue[assigned_id] += 1

    rows = [
        [p.display_name, p.get_role_display(), p.open_total, p.monitoring, p.pending,
         overdue.get(p.pk, 0), p.exceptions, p.completed]
        for p in people
    ]
    return ReportData(
        title="Staff Workload",
        columns=["Staff", "Role", "Open orders", "Monitoring", "Pending review",
                 "Overdue reviews", "Exceptions", "Completed"],
        rows=rows,
        summary=[("Staff members", len(rows)), ("Open orders (total)", sum(r[2] for r in rows))],
        note="Completed: when a date filter is applied, only orders closed in that range.",
    )


# ---------- 5. Account health ----------
def account_health(user, f):
    qs = GoogleAccountConnection.objects.select_related("connected_by").order_by("label")
    if f.get("status"):
        qs = qs.filter(status=f["status"])
    connections, note = _cap(qs)

    counts = defaultdict(int)
    for c in connections:
        counts[c.get_status_display()] += 1
    rows = [
        [c.label, c.google_email, c.get_status_display(),
         c.connected_by.display_name if c.connected_by else "", _dt(c.connected_at),
         _dt(c.last_checked_at), c.last_error]
        for c in connections
    ]
    return ReportData(
        title="Account Health",
        columns=["Account", "Google email", "Status", "Connected by", "Connected on",
                 "Last successful check", "Last error"],
        rows=rows,
        summary=[("Accounts", len(connections)), *sorted(counts.items())],
        note=note,
    )


# ---------- 6. Audit ----------
def _audit_details(entry):
    parts = []
    for name, value in (entry.changes or {}).items():
        if isinstance(value, list) and len(value) == 2:
            parts.append(f"{name}: {value[0]} -> {value[1]}")
        else:
            parts.append(f"{name}: {value}")
    text = "; ".join(parts)
    if entry.ip_address:
        text = f"{text} [IP {entry.ip_address}]".strip()
    return text


def audit_report(user, f):
    start, end, note = _date_range(f, 7)
    staff = f.get("staff")

    order_events = OrderEvent.objects.select_related("order", "actor").filter(
        created_at__date__range=(start, end)
    )
    connection_events = ConnectionEvent.objects.select_related("connection", "actor").filter(
        created_at__date__range=(start, end)
    )
    audit_entries = AuditLog.objects.select_related("actor").filter(
        created_at__date__range=(start, end)
    )
    if staff:
        order_events = order_events.filter(actor=staff)
        connection_events = connection_events.filter(actor=staff)
        audit_entries = audit_entries.filter(actor=staff)

    order_events = list(order_events.order_by("-created_at")[:MAX_AUDIT_ROWS])
    connection_events = list(connection_events.order_by("-created_at")[:MAX_AUDIT_ROWS])
    audit_entries = list(audit_entries.order_by("-created_at")[:MAX_AUDIT_ROWS])

    items = [
        (e.created_at, "Order", e.order.order_number, e.action,
         e.actor.display_name if e.actor else "System", e.note)
        for e in order_events
    ] + [
        (e.created_at, "Google account", e.connection.label, e.action,
         e.actor.display_name if e.actor else "System", e.note)
        for e in connection_events
    ] + [
        (e.created_at, e.entity_type or "System", e.entity_label or e.entity_id, e.action,
         e.actor.display_name if e.actor else "System", _audit_details(e))
        for e in audit_entries
    ]
    items.sort(key=lambda item: item[0], reverse=True)
    if len(items) > MAX_AUDIT_ROWS:
        note = (note + " " if note else "") + f"Sirf latest {MAX_AUDIT_ROWS} entries."
        items = items[:MAX_AUDIT_ROWS]

    rows = [[_dt(when), source, record, action, actor, details]
            for when, source, record, action, actor, details in items]
    return ReportData(
        title="Audit Report",
        columns=["When", "Source", "Record", "Action", "By", "Details"],
        rows=rows,
        summary=[("Entries", len(rows)), ("Range", f"{start:%d %b %Y} - {end:%d %b %Y}")],
        note=note,
    )