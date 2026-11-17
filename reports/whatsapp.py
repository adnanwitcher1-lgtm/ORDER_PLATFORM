"""Automatic WhatsApp messages through the Meta WhatsApp Business Cloud API.

WhatsApp only lets a business start a conversation with an APPROVED message template,
so the platform sends a template with two variables: {{1}} customer name, {{2}} portal link.
Suggested template text (category Utility, name weekly_progress_report):

    Hello {{1}}, your weekly progress report is ready. Follow your orders live here: {{2}}
"""
import requests
from django.conf import settings
from django.utils import timezone

from audit.services import log_audit

from .client_report import has_reportable_orders
from .delivery import portal_url_for

TIMEOUT_SECONDS = 20


class WhatsAppError(Exception):
    """Error that is safe to show to the user (never contains the access token)."""


def is_configured():
    return bool(getattr(settings, "WHATSAPP_TOKEN", "") and getattr(settings, "WHATSAPP_PHONE_NUMBER_ID", ""))


def build_payload(number, name, link):
    return {
        "messaging_product": "whatsapp",
        "to": number,
        "type": "template",
        "template": {
            "name": settings.WHATSAPP_TEMPLATE_NAME,
            "language": {"code": settings.WHATSAPP_TEMPLATE_LANG},
            "components": [{
                "type": "body",
                "parameters": [
                    {"type": "text", "text": (name or "there")[:60]},
                    {"type": "text", "text": link},
                ],
            }],
        },
    }


def send_template(number, name, link):
    """POST the template message. Returns the WhatsApp message id."""
    if not is_configured():
        raise WhatsAppError("WhatsApp is not configured (WHATSAPP_TOKEN and WHATSAPP_PHONE_NUMBER_ID in .env).")
    url = f"https://graph.facebook.com/{settings.WHATSAPP_API_VERSION}/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
    try:
        response = requests.post(
            url, json=build_payload(number, name, link), timeout=TIMEOUT_SECONDS,
            headers={"Authorization": f"Bearer {settings.WHATSAPP_TOKEN}"},
        )
    except requests.RequestException:
        raise WhatsAppError("Could not reach WhatsApp. Please try again.")
    try:
        body = response.json()
    except ValueError:
        body = {}
    if response.status_code >= 400:
        err = body.get("error", {}) if isinstance(body, dict) else {}
        detail = str(err.get("message", ""))[:160]
        raise WhatsAppError(f"WhatsApp refused the message ({response.status_code}). {detail}".strip())
    try:
        return body["messages"][0]["id"]
    except (KeyError, IndexError, TypeError):
        raise WhatsAppError("WhatsApp returned an unexpected answer.")


def send_whatsapp_report(customer, request=None, user=None):
    number = customer.whatsapp_number()
    if not number:
        raise WhatsAppError("This customer has no valid phone number.")
    link = portal_url_for(customer, request)
    if not link:
        raise WhatsAppError("Turn on the client portal link first: the message contains it.")
    if not has_reportable_orders(customer):
        raise WhatsAppError("This customer has no orders to report yet.")
    message_id = send_template(number, customer.name, link)
    customer.last_whatsapp_sent_at = timezone.now()
    customer.save(update_fields=["last_whatsapp_sent_at"])
    log_audit(request, "report sent on WhatsApp", customer, entity_type="Customer", actor=user)
    return message_id
