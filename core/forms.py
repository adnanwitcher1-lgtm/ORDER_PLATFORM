from django import forms

from .models import SiteSettings


class StyledFormMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "input")


class SiteSettingsForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = SiteSettings
        fields = [
            "monitoring_interval_minutes", "retry_base_minutes", "max_failures",
            "review_overdue_hours", "drop_alert_percent", "stalled_alert_hours", "audit_retention_days", "notification_retention_days",
        ]