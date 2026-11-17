import secrets
import re
from urllib.parse import quote

from django.conf import settings
from django.db import models
from django.utils import timezone


class Customer(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        ARCHIVED = "archived", "Archived"

    name = models.CharField(max_length=150)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="customers_created",
    )
    # ---- Client portal (read-only link) and weekly report ----
    portal_enabled = models.BooleanField(default=False)
    portal_token = models.CharField(max_length=64, unique=True, null=True, blank=True, editable=False)
    portal_token_created_at = models.DateTimeField(null=True, blank=True, editable=False)
    portal_last_viewed_at = models.DateTimeField(null=True, blank=True, editable=False)
    weekly_report_enabled = models.BooleanField(
        "Send weekly report by email", default=False,
        help_text="A PDF progress report is emailed to the customer every week (needs an email address).",
    )
    last_report_sent_at = models.DateTimeField(null=True, blank=True, editable=False)
    weekly_whatsapp_enabled = models.BooleanField(
        "Send weekly WhatsApp message", default=False,
        help_text="Needs a phone number, an active portal link and the WhatsApp API settings.",
    )
    last_whatsapp_sent_at = models.DateTimeField(null=True, blank=True, editable=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name

    # ---------- portal ----------
    def new_portal_token(self):
        """Create a fresh random token (the old link stops working) and enable the portal."""
        self.portal_token = secrets.token_urlsafe(32)
        self.portal_token_created_at = timezone.now()
        self.portal_enabled = True

    def disable_portal(self):
        self.portal_enabled = False

    @property
    def portal_is_live(self):
        return bool(self.portal_enabled and self.portal_token and self.status == self.Status.ACTIVE)

    def whatsapp_number(self):
        """Digits only, with the country code (a local number starting with 0 gets DEFAULT_COUNTRY_CODE)."""
        digits = re.sub(r"\D", "", self.phone or "")
        if digits.startswith("00"):
            digits = digits[2:]
        elif digits.startswith("0"):
            digits = getattr(settings, "DEFAULT_COUNTRY_CODE", "92") + digits[1:]
        return digits if len(digits) >= 8 else ""

    def whatsapp_link(self, message):
        number = self.whatsapp_number()
        if not number:
            return ""
        return f"https://wa.me/{number}?text={quote(message)}"
