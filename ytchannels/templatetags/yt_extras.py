from django import template

register = template.Library()


@register.filter
def compact(value):
    """12345 -> 12.3K, 2500000 -> 2.5M. None -> a dash."""
    if value is None or value == "":
        return "-"
    try:
        n = float(value)
    except (TypeError, ValueError):
        return "-"
    for limit, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if abs(n) >= limit:
            text = f"{n / limit:.1f}".rstrip("0").rstrip(".")
            return f"{text}{suffix}"
    return f"{int(n):,}"


@register.filter
def full(value):
    if value is None or value == "":
        return "-"
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "-"


@register.filter
def duration(seconds):
    """3725 -> 1:02:05, 95 -> 1:35."""
    if seconds is None or seconds == "":
        return "-"
    seconds = int(seconds)
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
