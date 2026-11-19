from django.conf import settings
from django.db import models

from customers.models import Customer


class Channel(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        ARCHIVED = "archived", "Archived"

    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="channels")
    name = models.CharField(max_length=150)
    url = models.URLField(max_length=300, unique=True)
    youtube_channel_id = models.CharField("YouTube channel ID", max_length=60, blank=True)
    authorization_notes = models.TextField(
        blank=True,
        help_text="Ownership / authorization notes. Do not enter passwords or recovery codes here.",
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    google_connection = models.ForeignKey(
        "googleaccounts.GoogleAccountConnection", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="channels",
        help_text="The connected Google account this channel was imported from.",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="channels_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name


class ChannelInsight(models.Model):
    """Latest public details of a channel (from the YouTube API) and an optional AI summary."""

    channel = models.OneToOneField(Channel, on_delete=models.CASCADE, related_name="insight")
    data = models.JSONField(default=dict)
    fetched_at = models.DateTimeField()
    fetched_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    ai_summary = models.TextField(blank=True)
    ai_source = models.CharField(max_length=10, blank=True)   # "ai" or "rules"
    ai_lang = models.CharField(max_length=2, blank=True)
    ai_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Insight for {self.channel}"



class ChannelAnalytics(models.Model):
    """Owner-only YouTube Analytics numbers (watch time, exact subscriber gain). Not real-time: 1-3 days late."""

    channel = models.OneToOneField(Channel, on_delete=models.CASCADE, related_name="analytics")
    data = models.JSONField(default=dict)
    fetched_at = models.DateTimeField()
    fetched_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    def __str__(self):
        return f"Analytics of {self.channel_id}"
