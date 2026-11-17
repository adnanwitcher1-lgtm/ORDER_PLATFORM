from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin

from audit.mixins import AuditedAdminMixin

from audit.services import log_audit

from . import twofactor
from .models import User

admin.site.site_header = "Order Platform Admin"
admin.site.site_title = "Order Platform"
admin.site.index_title = "Administration"


@admin.register(User)
class CustomUserAdmin(AuditedAdminMixin, UserAdmin):
    actions = ["reset_two_factor"]
    list_display = ("username", "email", "role", "is_active", "last_login")
    list_filter = ("role", "is_active")
    fieldsets = UserAdmin.fieldsets + (("Role & Contact", {"fields": ("role", "phone")}),)
    add_fieldsets = UserAdmin.add_fieldsets + (
        ("Role & Contact", {"fields": ("role", "phone")}),
    )

    @admin.action(description="Reset two-factor authentication (user lost their device)")
    def reset_two_factor(self, request, queryset):
        count = 0
        for user in queryset:
            if twofactor.has_two_factor(user):
                twofactor.disable(user)
                log_audit(request, "2FA reset by admin", user, entity_type="Login", entity_label=user.username)
                count += 1
        self.message_user(request, f"Two-factor authentication reset for {count} user(s).", messages.SUCCESS)
