from django.conf import settings
from django.db import models
from django.utils import timezone

from .crypto import decrypt_token, encrypt_token


class GoogleAccountConnection(models.Model):
    class Status(models.TextChoices):
        CONNECTED = "connected", "Connected"
        NEEDS_REAUTH = "needs_reauth", "Needs Reauthorization"
        DISCONNECTED = "disconnected", "Disconnected"
        ERROR = "error", "Error"
        DISABLED = "disabled", "Disabled"

    label = models.CharField(max_length=100)
    google_email = models.EmailField(blank=True)
    google_sub = models.CharField(max_length=64, unique=True)
    scopes = models.TextField(blank=True)
    encrypted_refresh_token = models.TextField(blank=True)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.CONNECTED)
    notes = models.TextField(
        blank=True,
        help_text="Operational notes. Do not enter passwords or recovery codes here.",
    )
    connected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="google_connections",
    )
    connected_at = models.DateTimeField(default=timezone.now)
    last_checked_at = models.DateTimeField(null=True, blank=True)   # last SUCCESSFUL check
    last_error = models.CharField(max_length=200, blank=True)
    disconnected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    disconnected_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["label"]

    def __str__(self):
        return f"{self.label} ({self.google_email})"

    @property
    def has_token(self):
        return bool(self.encrypted_refresh_token)

    def set_refresh_token(self, token):
        self.encrypted_refresh_token = encrypt_token(token)

    def get_refresh_token(self):
        return decrypt_token(self.encrypted_refresh_token) if self.encrypted_refresh_token else ""


class ConnectionEvent(models.Model):
    """Audit history of the connection."""

    connection = models.ForeignKey(
        GoogleAccountConnection, on_delete=models.CASCADE, related_name="events"
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    action = models.CharField(max_length=80)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]