from django.conf import settings
from django.db import models


class Notification(models.Model):
    class Kind(models.TextChoices):
        ASSIGNMENT = "assignment", "Assignment"
        TARGET_OBSERVED = "target_observed", "Target observed"
        EXCEPTION = "exception", "Exception"
        API_ERROR = "api_error", "API error"
        REAUTH = "reauth", "Reauthorization"
        REVIEW_OVERDUE = "review_overdue", "Review overdue"
        ANOMALY = "anomaly", "Anomaly"

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications"
    )
    kind = models.CharField(max_length=20, choices=Kind.choices)
    message = models.CharField(max_length=255)
    link = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [models.Index(fields=["recipient", "read_at"])]

    def __str__(self):
        return f"{self.recipient} - {self.message}"

    @property
    def is_read(self):
        return self.read_at is not None