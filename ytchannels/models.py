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