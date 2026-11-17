from django import forms

from core.forms import StyledFormMixin

from .models import GoogleAccountConnection


class ConnectForm(StyledFormMixin, forms.Form):
    label = forms.CharField(max_length=100, label="Account label (internal naam)")
    notes = forms.CharField(
        required=False, label="Notes",
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Do not enter passwords or recovery codes here.",
    )
    confirm_authorized = forms.BooleanField(
        label="I confirm this Google account belongs to the client, or I have explicit authorization to administer it",
        error_messages={"required": "Authorization confirmation is required."},
        widget=forms.CheckboxInput(attrs={"class": "checkbox"}),
    )


class ConnectionEditForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = GoogleAccountConnection
        fields = ["label", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}