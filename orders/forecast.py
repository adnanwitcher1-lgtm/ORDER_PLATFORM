"""Growth series and ETA estimate for an order.

The estimate is a simple least-squares trend over the recent observations.
It is only an estimate based on observed counts, never a promise.
"""
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

from django.utils import timezone

from .models import MetricObservation

WINDOW_DAYS = 7              # trend window (falls back to all points if too few)
MIN_SPAN_HOURS = 2           # need at least this much time between first and last point
MAX_ETA_DAYS = 365
MAX_POINTS = 300


@dataclass(frozen=True)
class Eta:
    state: str                       # reached | no_data | insufficient | stalled | declining | ok | far
    message: str
    rate_per_day: Optional[float] = None
    days: Optional[float] = None
    eta: Optional[object] = None     # datetime


def valid_points(order):
    """[(datetime, count)] oldest first: the baseline followed by every usable observation."""
    rows = (
        MetricObservation.objects.filter(order=order, observed_count__isnull=False)
        .exclude(accuracy=MetricObservation.Accuracy.UNAVAILABLE)
        .order_by("-observed_at", "-pk")
        .values_list("observed_at", "observed_count")[:MAX_POINTS]
    )
    points = [(when, count) for when, count in reversed(list(rows))]
    baseline = (order.baseline_recorded_at, order.baseline_count)
    if not points or baseline[0] < points[0][0]:
        points.insert(0, baseline)
    return points


def _slope_per_day(points):
    """Least-squares slope (count per day) for [(datetime, count)]."""
    t0 = points[0][0]
    xs = [(when - t0).total_seconds() / 86400 for when, _ in points]
    ys = [count for _, count in points]
    n = len(points)
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    denom = sum((x - mean_x) ** 2 for x in xs)
    if denom == 0:
        return 0.0
    return sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denom


def estimate_eta(order, now=None, points=None) -> Eta:
    now = now or timezone.now()
    latest = order.latest_observed_count
    if latest is None:
        return Eta("no_data", "No count observed yet, so no estimate is possible.")
    if latest >= order.target_count:
        return Eta("reached", "The target has been observed.")

    points = points if points is not None else valid_points(order)
    recent = [p for p in points if p[0] >= now - timedelta(days=WINDOW_DAYS)]
    use = recent if len(recent) >= 2 else points
    if len(use) < 2 or (use[-1][0] - use[0][0]) < timedelta(hours=MIN_SPAN_HOURS):
        return Eta("insufficient", "Not enough observations over time yet for an estimate.")

    rate = _slope_per_day(use)
    if rate <= 0:
        state = "declining" if rate < 0 else "stalled"
        text = "The count has been falling recently." if rate < 0 else "The count has not grown recently."
        return Eta(state, text + " No estimate is possible.", rate_per_day=round(rate, 1))

    days = (order.target_count - latest) / rate
    if days > MAX_ETA_DAYS:
        return Eta("far", "At the current pace the target is more than a year away.",
                   rate_per_day=round(rate, 1), days=days)
    eta = now + timedelta(days=days)
    if days < 1:
        when = "less than a day"
    elif days < 1.5:
        when = "about 1 day"
    else:
        when = f"about {round(days)} days"
    return Eta(
        "ok",
        f"Estimate: target in {when} (around {round(rate):,} per day at the current pace).",
        rate_per_day=round(rate, 1), days=days, eta=eta,
    )


def chart_payload(order, now=None, include_estimate=True):
    """JSON-ready data for the growth chart (staff page and client portal)."""
    now = now or timezone.now()
    points = valid_points(order)
    eta = estimate_eta(order, now=now, points=points)
    if not include_estimate:
        eta = Eta("hidden", "")

    local = timezone.localtime
    labels = [local(when).strftime("%d %b %H:%M") for when, _ in points]
    values = [count for _, count in points]
    projection = [None] * len(points)

    if eta.state == "ok" and eta.eta is not None:
        labels.append(local(eta.eta).strftime("%d %b %H:%M") + " (est.)")
        values.append(None)
        projection[-1] = values[-2] if len(values) > 1 else None
        projection.append(order.target_count)

    return {
        "labels": labels,
        "values": values,
        "projection": projection,
        "target": order.target_count,
        "baseline": order.baseline_count,
        "eta": {"state": eta.state, "message": eta.message},
    }
