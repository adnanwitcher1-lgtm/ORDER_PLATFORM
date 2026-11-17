from django import forms

from core.forms import StyledFormMixin

from .models import Customer


class CustomerForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Customer
        fields = ["name", "email", "phone", "weekly_report_enabled", "weekly_whatsapp_enabled", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 4})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["weekly_report_enabled"].widget.attrs["class"] = "checkbox"
        self.fields["weekly_whatsapp_enabled"].widget.attrs["class"] = "checkbox"

    def clean(self):
        data = super().clean()
        if data.get("weekly_report_enabled") and not data.get("email"):
            self.add_error("email", "An email address is required to send weekly reports.")
        if data.get("weekly_whatsapp_enabled"):
            from .models import Customer
            if not Customer(phone=data.get("phone", "")).whatsapp_number():
                self.add_error("phone", "A valid phone number is required to send WhatsApp messages.")
        return data