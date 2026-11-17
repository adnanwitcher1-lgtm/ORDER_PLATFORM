"""Plain-language summaries and smart (natural language) order search.

Both work with simple rules, so they run offline and for free. When an AI key is
configured, the summary is polished by Claude (from the same facts) and the smart
search can fall back to Claude for sentences the rules do not understand.
"""
import json
import re
from dataclasses import dataclass, field
from datetime import timedelta

from django.db.models import Max, Q
from django.utils import timezone

from core import ai
from core.models import SiteSettings

from .anomalies import find_stalled
from .forecast import estimate_eta
from .models import Order

S = Order.Status

STATUS_UR = {
    S.DRAFT: "draft", S.PENDING_REVIEW: "approval ke intezar mein", S.APPROVED: "approved",
    S.ASSIGNED: "assign ho chuke", S.MONITORING: "monitoring mein", S.PAUSED: "paused",
    S.TARGET_OBSERVED: "review ke intezar mein (target observe ho gaya)",
    S.COMPLETED: "completed", S.CANCELLED: "cancelled", S.EXCEPTION: "exception mein",
}
STATUS_EN = {
    S.DRAFT: "in draft", S.PENDING_REVIEW: "awaiting approval", S.APPROVED: "approved",
    S.ASSIGNED: "assigned", S.MONITORING: "being monitored", S.PAUSED: "paused",
    S.TARGET_OBSERVED: "awaiting review (target observed)", S.COMPLETED: "completed",
    S.CANCELLED: "cancelled", S.EXCEPTION: "in exception",
}
ISSUE_EN = {"review": "review pending", "exception": "in exception", "paused": "paused", "stalled": "no growth"}
ISSUE_UR = {"review": "review baqi", "exception": "exception mein", "paused": "paused", "stalled": "growth ruk gayi"}


# ----------------------------------------------------------------------------- facts
def _stalled_numbers():
    return {o.pk for o, _ in find_stalled()}


def order_facts(order):
    progress = order.progress
    eta = estimate_eta(order)
    hours = None
    if order.latest_observed_at:
        hours = round((timezone.now() - order.latest_observed_at).total_seconds() / 3600, 1)
    return {
        "order": order.order_number,
        "customer": order.customer.name,
        "channel": order.channel.name,
        "status": order.status,
        "status_label": order.get_status_display(),
        "baseline": order.baseline_count,
        "target": order.target_count,
        "latest": order.latest_observed_count,
        "percent": progress.progress_percent if progress.has_observation else None,
        "remaining": progress.remaining_gap,
        "eta_state": eta.state,
        "eta_days": round(eta.days, 1) if eta.days is not None else None,
        "eta_rate_per_day": eta.rate_per_day,
        "hours_since_last_check": hours,
        "stalled": order.pk in _stalled_numbers(),
    }


def _issue(order, stalled_ids):
    if order.status == S.TARGET_OBSERVED:
        return "review"
    if order.status == S.EXCEPTION:
        return "exception"
    if order.status == S.PAUSED:
        return "paused"
    if order.pk in stalled_ids:
        return "stalled"
    return ""


def group_facts(orders, label):
    stalled_ids = _stalled_numbers()
    counts, attention, percents = {}, [], []
    for order in orders:
        counts[order.status] = counts.get(order.status, 0) + 1
        issue = _issue(order, stalled_ids)
        if issue:
            attention.append({"order": order.order_number, "issue": issue})
        if order.status in (S.MONITORING, S.ASSIGNED) and order.progress.has_observation:
            percents.append(order.progress.progress_percent)
    return {
        "scope": label,
        "total": sum(counts.values()),
        "by_status": counts,
        "attention": attention[:8],
        "attention_total": len(attention),
        "average_progress": round(sum(percents) / len(percents)) if percents else None,
    }


def customer_facts(customer):
    facts = group_facts(Order.objects.filter(customer=customer), customer.name)
    facts["customer"] = customer.name
    return facts


def portfolio_facts():
    facts = group_facts(Order.objects.all(), "all customers")
    from customers.models import Customer
    facts["customers"] = Customer.objects.filter(status=Customer.Status.ACTIVE).count()
    return facts


# ------------------------------------------------------------------ rule summaries
def _join(parts, last="and"):
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + f" {last} " + parts[-1]


def _status_parts(by_status, labels):
    order = [S.MONITORING, S.ASSIGNED, S.APPROVED, S.TARGET_OBSERVED, S.PAUSED, S.EXCEPTION,
             S.PENDING_REVIEW, S.DRAFT, S.COMPLETED, S.CANCELLED]
    return [f"{by_status[s]} {labels[s]}" for s in order if by_status.get(s)]


def rule_summary(kind, facts, lang="en"):
    ur = lang == "ur"
    if kind == "order":
        f = facts
        status = (STATUS_UR if ur else STATUS_EN)[f["status"]]
        if f["latest"] is None:
            progress = ("abhi koi count nazar nahi aaya." if ur else "no count has been observed yet.")
        elif ur:
            progress = f"{f['latest']:,} observe hue, target {f['target']:,} ({f['percent']}%), {f['remaining']:,} baqi."
        else:
            progress = f"{f['latest']:,} observed against a target of {f['target']:,} ({f['percent']}%), {f['remaining']:,} to go."
        extra = ""
        if f["eta_state"] == "ok":
            days = f["eta_days"]
            extra = (f" Andaza: taqreeban {max(round(days), 1)} din mein target." if ur
                     else f" Estimate: target in about {max(round(days), 1)} day(s).")
        elif f["stalled"]:
            extra = " Pichle kuch waqt se growth ruki hui hai." if ur else " Growth has stalled recently."
        head = f"{f['order']} ({f['customer']}, {f['channel']}) " + (f"ka status: {status}. " if ur else f"is {status}: ")
        return (head + progress + extra).strip()

    labels = STATUS_UR if ur else STATUS_EN
    name = facts.get("customer")
    if facts["total"] == 0:
        return (f"{name} ke abhi koi orders nahi hain." if ur else f"{name} has no orders yet.") if name else \
               ("Abhi koi order nahi hai." if ur else "There are no orders yet.")
    parts = _status_parts(facts["by_status"], labels)
    who = name or ("Platform" if not ur else "Platform")
    if ur:
        text = f"{who} ke {facts['total']} orders hain: {_join(parts, 'aur')}."
    else:
        text = f"{who} has {facts['total']} order{'s' if facts['total'] != 1 else ''}: {_join(parts)}."
    if facts.get("average_progress") is not None:
        text += (f" Chalte orders ki average progress {facts['average_progress']}% hai." if ur
                 else f" Average progress of running orders is {facts['average_progress']}%.")
    if facts["attention"]:
        issues = ISSUE_UR if ur else ISSUE_EN
        items = [f"{a['order']} ({issues[a['issue']]})" for a in facts["attention"][:4]]
        more = facts["attention_total"] - len(items)
        tail = (f" aur {more} aur" if ur else f" and {more} more") if more > 0 else ""
        text += (" Tawajjo chahiye: " if ur else " Needs attention: ") + ", ".join(items) + tail + "."
    else:
        text += " Sab theek chal raha hai." if ur else " Nothing needs attention right now."
    return text


def summarize(kind, facts, lang, user):
    """Return (text, source, note). Uses Claude when configured, otherwise the rules."""
    base = rule_summary(kind, facts, lang)
    if not ai.is_configured():
        return base, "rules", ""
    if not ai.within_limit(user):
        return base, "rules", "The hourly AI limit was reached, so a rule-based summary is shown."
    language = "Roman Urdu (Urdu written in English letters)" if lang == "ur" else "English"
    system = (
        "You write one short status summary for a staff dashboard. Use ONLY the facts given. "
        "Never invent numbers or dates. Counts are observed values, so do not promise or guarantee results. "
        "Keep order numbers exactly as written. Maximum 3 short sentences. Plain text only."
    )
    prompt = f"Language: {language}\nFacts (JSON): {json.dumps(facts, default=str)}\nDraft to improve: {base}"
    try:
        text = ai.ask(system, prompt, max_tokens=260)
    except ai.AIError as exc:
        return base, "rules", f"{exc} A rule-based summary is shown."
    return text[:900], "ai", ""


# ------------------------------------------------------------------- smart search
@dataclass
class Parsed:
    statuses: set = field(default_factory=set)
    priority: str = ""
    customer_ids: list = field(default_factory=list)
    customer_names: list = field(default_factory=list)
    staff_ids: list = field(default_factory=list)
    staff_names: list = field(default_factory=list)
    stalled: bool = False
    overdue: bool = False
    unassigned: bool = False
    mine: bool = False
    since: object = None
    since_label: str = ""
    used_ai: bool = False

    @property
    def understood(self):
        return bool(
            self.statuses or self.priority or self.customer_ids or self.staff_ids or self.stalled
            or self.overdue or self.unassigned or self.mine or self.since
        )

    def chips(self):
        labels = dict(Order.Status.choices)
        chips = [f"Status: {labels[s]}" for s in sorted(self.statuses)]
        if self.priority:
            chips.append(f"Priority: {self.priority}")
        chips += [f"Customer: {n}" for n in self.customer_names]
        chips += [f"Assigned to: {n}" for n in self.staff_names]
        if self.mine:
            chips.append("Assigned to me")
        if self.unassigned:
            chips.append("Unassigned")
        if self.stalled:
            chips.append("No growth")
        if self.overdue:
            chips.append("Review overdue")
        if self.since_label:
            chips.append(f"Created: {self.since_label}")
        return chips


STATUS_PATTERNS = [
    (r"\bpending\b|awaiting approval|\bapproval\b", S.PENDING_REVIEW),
    (r"target observed|\bobserved\b|awaiting review|needs? review|review required|\breview\b", S.TARGET_OBSERVED),
    (r"\bdrafts?\b", S.DRAFT),
    (r"\bapproved\b", S.APPROVED),
    (r"\bassigned\b", S.ASSIGNED),
    (r"\bmonitoring\b|in progress|\brunning\b|\bchal(ta|te|ti)?\b", S.MONITORING),
    (r"\bpaused?\b|on hold|\bruk(a|e|i)\b|\bruke hue\b", S.PAUSED),
    (r"\bcompleted?\b|\bdone\b|\bfinished\b|\bmukammal\b", S.COMPLETED),
    (r"\bcancel(l)?ed\b|\bcancel\b", S.CANCELLED),
    (r"\bexceptions?\b|\bfailed\b|\berrors?\b|\bmasla\b", S.EXCEPTION),
]
PRIORITY_PATTERNS = [
    (r"\bhigh\b|\burgent\b|\bzaroori\b|\bhigh priority\b", "high"),
    (r"\blow\b", "low"),
    (r"\bnormal\b", "normal"),
]


def _date_since(text, now):
    today = now.astimezone(timezone.get_current_timezone()).replace(hour=0, minute=0, second=0, microsecond=0)
    if re.search(r"\btoday\b|\baaj\b", text):
        return today, "today"
    if re.search(r"\byesterday\b|\bkal\b", text):
        return today - timedelta(days=1), "since yesterday"
    if re.search(r"this week|last 7 days|last week|is hafte|past week", text):
        return today - timedelta(days=7), "last 7 days"
    if re.search(r"this month|last 30 days|is mahine", text):
        return today - timedelta(days=30), "last 30 days"
    return None, ""


def parse_search(text, user=None, now=None):
    from accounts.models import User
    from customers.models import Customer

    now = now or timezone.now()
    low = (text or "").lower()
    parsed = Parsed()

    # "pending review" means waiting for approval, not "target observed"
    if re.search(r"pending review|review pending", low):
        parsed.statuses.add(S.PENDING_REVIEW)
        low = re.sub(r"pending review|review pending", " ", low)

    for pattern, status in STATUS_PATTERNS:
        if re.search(pattern, low):
            parsed.statuses.add(status)
    if re.search(r"\bactive\b", low) and not parsed.statuses:
        parsed.statuses.update(Order.ACTIVE_STATUSES)
    for pattern, value in PRIORITY_PATTERNS:
        if re.search(pattern, low):
            parsed.priority = value
            break

    for customer in Customer.objects.all():
        name = customer.name.lower().strip()
        if len(name) >= 2 and re.search(rf"(?<!\w){re.escape(name)}(?!\w)", low):
            parsed.customer_ids.append(customer.pk)
            parsed.customer_names.append(customer.name)

    for staff in User.objects.filter(is_active=True):
        tokens = {staff.username.lower()}
        if staff.first_name:
            tokens.add(staff.first_name.lower())
        if any(len(t) >= 3 and re.search(rf"(?<!\w){re.escape(t)}(?!\w)", low) for t in tokens):
            parsed.staff_ids.append(staff.pk)
            parsed.staff_names.append(staff.display_name)

    parsed.stalled = bool(re.search(r"\bstalled\b|\bstuck\b|\bslow\b|no growth|\bruka hua\b|\bdheema\b", low))
    parsed.overdue = bool(re.search(r"\boverdue\b|\blate\b|\bdelayed\b", low))
    parsed.unassigned = bool(re.search(r"\bunassigned\b|not assigned|no one assigned", low))
    parsed.mine = bool(re.search(r"\bmy orders\b|\bmine\b|\bmere\b|assigned to me", low))
    parsed.since, parsed.since_label = _date_since(low, now)

    if not parsed.understood and ai.is_configured() and user is not None and ai.within_limit(user):
        _ai_parse(text, parsed, now)
    return parsed


def _ai_parse(text, parsed, now):
    """Ask Claude to turn the sentence into filters. The answer is validated strictly."""
    from customers.models import Customer

    names = list(Customer.objects.values_list("name", flat=True)[:100])
    system = (
        "Convert an order search sentence into JSON filters. Reply with JSON only, no text. "
        'Keys (all optional): "statuses" (list from: ' + ", ".join(Order.Status.values) + '), '
        '"priority" (low|normal|high), "customer" (one name from the provided list), '
        '"stalled" (bool), "overdue" (bool), "unassigned" (bool), "created" (today|yesterday|week|month).'
    )
    try:
        raw = ai.ask(system, f"Customers: {json.dumps(names)}\nSentence: {text[:300]}", max_tokens=200)
        data = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
    except (ai.AIError, ValueError):
        return
    if not isinstance(data, dict):
        return
    for status in data.get("statuses") or []:
        if status in Order.Status.values:
            parsed.statuses.add(status)
    if data.get("priority") in Order.Priority.values:
        parsed.priority = data["priority"]
    customer = Customer.objects.filter(name__iexact=str(data.get("customer", ""))).first()
    if customer:
        parsed.customer_ids.append(customer.pk)
        parsed.customer_names.append(customer.name)
    parsed.stalled = parsed.stalled or data.get("stalled") is True
    parsed.overdue = parsed.overdue or data.get("overdue") is True
    parsed.unassigned = parsed.unassigned or data.get("unassigned") is True
    created = {"today": 0, "yesterday": 1, "week": 7, "month": 30}.get(data.get("created"))
    if created is not None:
        today = now.astimezone(timezone.get_current_timezone()).replace(hour=0, minute=0, second=0, microsecond=0)
        parsed.since = today - timedelta(days=created)
        parsed.since_label = {0: "today", 1: "since yesterday", 7: "last 7 days", 30: "last 30 days"}[created]
    parsed.used_ai = parsed.understood


def apply_search(queryset, parsed, user, now=None):
    now = now or timezone.now()
    if parsed.statuses:
        queryset = queryset.filter(status__in=parsed.statuses)
    if parsed.priority:
        queryset = queryset.filter(priority=parsed.priority)
    if parsed.customer_ids:
        queryset = queryset.filter(customer_id__in=parsed.customer_ids)
    if parsed.staff_ids:
        queryset = queryset.filter(assigned_to_id__in=parsed.staff_ids)
    if parsed.mine and user is not None:
        queryset = queryset.filter(assigned_to=user)
    if parsed.unassigned:
        queryset = queryset.filter(assigned_to__isnull=True)
    if parsed.since:
        queryset = queryset.filter(created_at__gte=parsed.since)
    if parsed.stalled:
        queryset = queryset.filter(pk__in=_stalled_numbers())
    if parsed.overdue:
        hours = SiteSettings.load().review_overdue_hours
        late = (
            Order.objects.filter(status=S.TARGET_OBSERVED)
            .annotate(reached_at=Max("events__created_at", filter=Q(events__action="Target observed")))
            .filter(reached_at__lte=now - timedelta(hours=hours))
            .values_list("pk", flat=True)
        )
        queryset = queryset.filter(pk__in=list(late))
    return queryset
