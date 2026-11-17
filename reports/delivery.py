"""Emailing the client report (manually or from the weekly command)."""
from django.conf import settings
from django.core.mail import EmailMessage
from django.urls import reverse
from django.utils import timezone

from audit.services import log_audit

from .client_report import client_orders, render_pdf, summarize


class DeliveryError(Exception):
    """Error that is safe to show to the user."""


def portal_url_for(customer, request=None):
    if not customer.portal_is_live:
        return ""
    path = reverse("portal:home", args=[customer.portal_token])
    return request.build_absolute_uri(path) if request is not None else f"{settings.SITE_URL}{path}"


def send_customer_report(customer, request=None, user=None):
    if not customer.email:
        raise DeliveryError("This customer has no email address.")
    rows = client_orders(customer)
    if not rows:
        raise DeliveryError("This customer has no orders to report yet.")

    portal_url = portal_url_for(customer, request)
    try:
        pdf = render_pdf(customer, rows, portal_url)
    except ImportError:
        raise DeliveryError("PDF reports need reportlab: run 'pip install reportlab'.")

    stats = summarize(rows)
    brand = getattr(settings, "BRAND_NAME", "Order Platform")
    lines = [
        f"Hello {customer.name},",
        "",
        "Here is your progress report. The PDF is attached.",
        f"Orders: {stats['total']}  |  In progress: {stats['in_progress']}  |  "
        f"Completed: {stats['completed']}  |  Average progress: {stats['average']}%",
    ]
    if portal_url:
        lines += ["", f"You can also follow your orders live here: {portal_url}"]
    lines += ["", "Counts are observed values and may be rounded by YouTube.", "", f"{brand}"]

    message = EmailMessage(
        subject=f"{brand} - progress report for {customer.name}",
        body="\n".join(lines),
        to=[customer.email],
    )
    message.attach(f"progress-report-{timezone.localdate():%Y-%m-%d}.pdf", pdf, "application/pdf")
    try:
        message.send(fail_silently=False)
    except Exception as exc:  # noqa: BLE001 - SMTP errors vary; keep the message safe
        raise DeliveryError(f"The email could not be sent ({exc.__class__.__name__}). Check the email settings in .env.")

    customer.last_report_sent_at = timezone.now()
    customer.save(update_fields=["last_report_sent_at"])
    log_audit(request, "report emailed", customer, entity_type="Customer", actor=user)
    return customer.email
