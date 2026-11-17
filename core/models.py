from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class SiteSettings(models.Model):
    """A single row (pk=1). Created the first time from the .env/settings values."""

    monitoring_interval_minutes = models.PositiveIntegerField(
        "Monitoring interval (minutes)", default=60,
        validators=[MinValueValidator(1), MaxValueValidator(1440)],
        help_text="How often each order's YouTube count is checked. 30-60 is fine in production.",
    )
    retry_base_minutes = models.PositiveIntegerField(
        "Retry delay (minutes)", default=5,
        validators=[MinValueValidator(1), MaxValueValidator(60)],
        help_text="Delay before the first retry after a failure. It doubles each time (never more than the interval).",
    )
    max_failures = models.PositiveIntegerField(
        "Max consecutive failures", default=5,
        validators=[MinValueValidator(1), MaxValueValidator(20)],
        help_text="After this many consecutive failures the order moves to Exception.",
    )
    review_overdue_hours = models.PositiveIntegerField(
        "Review overdue (hours)", default=24,
        validators=[MinValueValidator(1), MaxValueValidator(720)],
        help_text="Alert if a target-observed order is still not reviewed after this many hours.",
    )
    drop_alert_percent = models.PositiveIntegerField(
        "Count drop alert (%)", default=3,
        validators=[MinValueValidator(1), MaxValueValidator(90)],
        help_text="Alert when a new count is lower than the previous one by at least this percentage.",
    )
    stalled_alert_hours = models.PositiveIntegerField(
        "Stalled growth alert (hours)", default=48,
        validators=[MinValueValidator(6), MaxValueValidator(720)],
        help_text="Alert when a monitored order has shown no growth for this many hours.",
    )
    audit_retention_days = models.PositiveIntegerField(
        "Audit log retention (days)", default=365,
        validators=[MinValueValidator(30), MaxValueValidator(3650)],
    )
    notification_retention_days = models.PositiveIntegerField(
        "Read notifications retention (days)", default=90,
        validators=[MinValueValidator(7), MaxValueValidator(3650)],
    )
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )

    class Meta:
        verbose_name_plural = "Site settings"

    def __str__(self):
        return "System settings"

    @classmethod
    def load(cls):
        defaults = {
            "monitoring_interval_minutes": getattr(settings, "MONITORING_INTERVAL_MINUTES", 60),
            "retry_base_minutes": getattr(settings, "MONITORING_RETRY_BASE_MINUTES", 5),
            "max_failures": getattr(settings, "MONITORING_MAX_FAILURES", 5),
            "review_overdue_hours": getattr(settings, "REVIEW_OVERDUE_HOURS", 24),
        }
        obj, _ = cls.objects.get_or_create(pk=1, defaults=defaults)
        return obj