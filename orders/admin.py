from django.contrib import admin

from audit.mixins import AuditedAdminMixin

from .models import MetricObservation, Order, OrderAssignmentHistory, OrderEvent


class ReadOnlyInline(admin.TabularInline):
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


class OrderEventInline(ReadOnlyInline):
    model = OrderEvent
    fields = ("created_at", "actor", "action", "from_status", "to_status", "note")
    readonly_fields = fields


class AssignmentInline(ReadOnlyInline):
    model = OrderAssignmentHistory
    fields = ("changed_at", "assigned_to", "changed_by", "reason")
    readonly_fields = fields


class ObservationInline(ReadOnlyInline):
    model = MetricObservation
    fields = ("observed_at", "observed_count", "accuracy", "source", "api_status", "recorded_by", "note")
    readonly_fields = fields


@admin.register(Order)
class OrderAdmin(AuditedAdminMixin, admin.ModelAdmin):
    list_display = (
        "order_number", "customer", "channel", "requested_increase",
        "baseline_count", "target_count", "latest_observed_count",
        "progress_display", "status", "assigned_to",
    )
    list_filter = ("status", "priority")
    search_fields = ("customer__name", "channel__name", "channel__url")
    readonly_fields = (
        "target_count", "created_at", "updated_at",
        "approved_at", "closed_at", "reviewed_by", "reviewed_at",
        "next_check_at", "check_failures",
    )
    inlines = [AssignmentInline, ObservationInline, OrderEventInline]

    @admin.display(description="Progress")
    def progress_display(self, obj):
        return f"{obj.progress.progress_percent}%"