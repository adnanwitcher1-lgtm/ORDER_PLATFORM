from django.contrib import admin

from audit.mixins import AuditedAdminMixin

from .models import Channel


@admin.register(Channel)
class ChannelAdmin(AuditedAdminMixin, admin.ModelAdmin):
    list_display = ("name", "customer", "url", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("name", "url", "customer__name")