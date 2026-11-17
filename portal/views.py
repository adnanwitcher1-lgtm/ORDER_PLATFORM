"""Public, read-only client portal. No login: access is by a long random link."""
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_GET

from audit.services import get_client_ip
from customers.models import Customer
from orders.forecast import chart_payload
from orders.models import Order
from reports.client_report import (
    CLIENT_STATUS, VISIBLE_STATUSES, client_orders, render_pdf, summarize,
)

MAX_BAD_TOKENS = 30
WINDOW_SECONDS = 15 * 60
VIEW_TOUCH_MINUTES = 10


def _secure(response):
    response["Cache-Control"] = "no-store, private"
    response["X-Robots-Tag"] = "noindex, nofollow"
    response["Referrer-Policy"] = "no-referrer"
    return response


def _customer_or_404(request, token):
    """Look up the customer; many wrong tokens from one address are throttled."""
    key = f"portal-bad:{get_client_ip(request) or 'unknown'}"
    if cache.get(key, 0) >= MAX_BAD_TOKENS:
        raise Http404
    customer = Customer.objects.filter(
        portal_token=token, portal_enabled=True, status=Customer.Status.ACTIVE
    ).first()
    if customer is None:
        try:
            cache.incr(key)
        except ValueError:
            cache.set(key, 1, WINDOW_SECONDS)
        raise Http404
    now = timezone.now()
    if not customer.portal_last_viewed_at or now - customer.portal_last_viewed_at > timedelta(minutes=VIEW_TOUCH_MINUTES):
        Customer.objects.filter(pk=customer.pk).update(portal_last_viewed_at=now)
    return customer


@require_GET
def portal_home(request, token):
    customer = _customer_or_404(request, token)
    rows = client_orders(customer)
    response = render(request, "portal/home.html", {
        "customer": customer,
        "rows": rows,
        "stats": summarize(rows),
        "token": token,
        "brand": settings.BRAND_NAME,
    })
    return _secure(response)


@require_GET
def portal_order(request, token, pk):
    customer = _customer_or_404(request, token)
    order = get_object_or_404(
        Order.objects.select_related("channel"),
        pk=pk, customer=customer, status__in=VISIBLE_STATUSES,
    )
    row = next((r for r in client_orders(customer) if r["pk"] == order.pk), None)
    show_eta = getattr(settings, "PORTAL_SHOW_ETA", True)
    response = render(request, "portal/order.html", {
        "customer": customer,
        "row": row,
        "token": token,
        "brand": settings.BRAND_NAME,
        "chart_data": chart_payload(order, include_estimate=show_eta),
        "show_eta": show_eta,
    })
    return _secure(response)


@require_GET
def portal_report(request, token):
    customer = _customer_or_404(request, token)
    try:
        pdf = render_pdf(customer, client_orders(customer))
    except ImportError:
        raise Http404
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="progress-report-{timezone.localdate():%Y-%m-%d}.pdf"'
    return _secure(response)
