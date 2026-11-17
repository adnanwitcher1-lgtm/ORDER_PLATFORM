from django.contrib import admin

from .models import ConnectionEvent, GoogleAccountConnection


class EventInline(admin.TabularInline):
    model = ConnectionEvent
    extra = 0
    can_delete = False
    fields = ("created_at", "actor", "action", "note")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(GoogleAccountConnection)
class GoogleAccountConnectionAdmin(admin.ModelAdmin):
    list_display = ("label", "google_email", "status", "connected_by", "last_checked_at")
    list_filter = ("status",)
    search_fields = ("label", "google_email")
    exclude = ("encrypted_refresh_token",)
    readonly_fields = (
        "google_sub", "scopes", "connected_by", "connected_at", "last_checked_at",
        "last_error", "disconnected_by", "disconnected_at", "created_at", "updated_at",
    )
    inlines = [EventInline]