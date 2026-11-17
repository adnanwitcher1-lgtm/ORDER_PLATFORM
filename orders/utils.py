from .models import Order

CLOSED_STATUSES = (Order.Status.COMPLETED, Order.Status.CANCELLED)


def find_duplicate_orders(channel, exclude_pk=None):
    """Any open (neither completed nor cancelled) order on the same channel."""
    qs = Order.objects.filter(channel=channel).exclude(status__in=CLOSED_STATUSES)
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    return qs.select_related("customer")