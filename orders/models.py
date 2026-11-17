from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from customers.models import Customer
from ytchannels.models import Channel

from .calculations import calculate_progress, calculate_target


class Order(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PENDING_REVIEW = "pending_review", "Pending Review"
        APPROVED = "approved", "Approved"
        ASSIGNED = "assigned", "Assigned"
        MONITORING = "monitoring", "Monitoring"
        PAUSED = "paused", "Paused"
        TARGET_OBSERVED = "target_observed", "Target Observed - Review Required"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"
        EXCEPTION = "exception", "Exception"

    class Priority(models.TextChoices):
        LOW = "low", "Low"
        NORMAL = "normal", "Normal"
        HIGH = "high", "High"

    # In these states the order is still being "chased".
    ACTIVE_STATUSES = (Status.APPROVED, Status.ASSIGNED, Status.MONITORING)
    # Baseline/increase can change only in these states (the target is recalculated).
    EDITABLE_STATUSES = (Status.DRAFT, Status.PENDING_REVIEW)

    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="orders")
    channel = models.ForeignKey(Channel, on_delete=models.PROTECT, related_name="orders")

    requested_increase = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    baseline_count = models.PositiveIntegerField()
    baseline_recorded_at = models.DateTimeField(default=timezone.now)
    target_count = models.PositiveIntegerField(editable=False)

    latest_observed_count = models.PositiveIntegerField(null=True, blank=True)
    latest_observed_at = models.DateTimeField(null=True, blank=True)
    latest_observed_accuracy = models.CharField(max_length=12, blank=True)
    next_check_at = models.DateTimeField(null=True, blank=True)
    check_failures = models.PositiveSmallIntegerField(default=0)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.NORMAL)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="assigned_orders",
    )
    notes = models.TextField(blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="orders_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="orders_reviewed",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.order_number} - {self.customer.name}"

    @property
    def order_number(self):
        return f"ORD-{self.pk:05d}" if self.pk else "ORD-NEW"

    @property
    def is_active(self):
        return self.status in self.ACTIVE_STATUSES

    @property
    def progress(self):
        return calculate_progress(
            self.baseline_count, self.requested_increase, self.latest_observed_count
        )

    def clean(self):
        if self.channel_id and self.customer_id and self.channel.customer_id != self.customer_id:
            raise ValidationError({"channel": "This channel does not belong to this customer."})

    def save(self, *args, **kwargs):
        # In draft/pending the target is recalculated. After approval the
        # target is locked so the starting point cannot change silently.
        if self.target_count is None or self.status in self.EDITABLE_STATUSES:
            self.target_count = calculate_target(self.baseline_count, self.requested_increase)
        super().save(*args, **kwargs)


class OrderAssignmentHistory(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="assignments")
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    changed_at = models.DateTimeField(auto_now_add=True)
    reason = models.TextField(blank=True)

    class Meta:
        ordering = ["-changed_at", "-pk"]
        verbose_name_plural = "Order assignment history"


class OrderEvent(models.Model):
    """Order activity log: every action, who did it, and why."""

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="events")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    action = models.CharField(max_length=60)
    from_status = models.CharField(max_length=20, blank=True)
    to_status = models.CharField(max_length=20, blank=True)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]

    @property
    def from_label(self):
        return dict(Order.Status.choices).get(self.from_status, "")

    @property
    def to_label(self):
        return dict(Order.Status.choices).get(self.to_status, "")


class MetricObservation(models.Model):
    class Source(models.TextChoices):
        MANUAL = "manual", "Manual entry"
        YOUTUBE_API = "youtube_api", "YouTube API"

    class Accuracy(models.TextChoices):
        EXACT = "exact", "Exact"
        APPROXIMATE = "approximate", "Approximate"
        UNAVAILABLE = "unavailable", "Unavailable"

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="observations")
    observed_count = models.PositiveIntegerField(null=True, blank=True)
    observed_at = models.DateTimeField(default=timezone.now)
    source = models.CharField(max_length=20, choices=Source.choices)
    accuracy = models.CharField(max_length=12, choices=Accuracy.choices)
    api_status = models.CharField(max_length=20, blank=True)
    note = models.TextField(blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )

    class Meta:
        ordering = ["-observed_at", "-pk"]

    def __str__(self):
        return f"{self.order.order_number} @ {self.observed_at:%d %b %H:%M}: {self.observed_count}"