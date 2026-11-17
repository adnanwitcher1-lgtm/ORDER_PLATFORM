from django.contrib import admin

from audit.mixins import AuditedAdminMixin

from .models import Customer


@admin.register(Customer)
class CustomerAdmin(AuditedAdminMixin, admin.ModelAdmin):
    list_display = ("name", "email", "phone", "status", "created_by", "created_at")
    list_filter = ("status",)
    search_fields = ("name", "email", "phone")