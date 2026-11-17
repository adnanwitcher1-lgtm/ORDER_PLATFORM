from django import forms

from accounts.models import User
from core.forms import StyledFormMixin
from customers.models import Customer
from ytchannels.models import Channel

from .models import MetricObservation, Order
from .utils import find_duplicate_orders


class OrderForm(StyledFormMixin, forms.ModelForm):
    confirm_duplicate = forms.BooleanField(
        required=False,
        label="I understand, create this order anyway",
    )

    class Meta:
        model = Order
        fields = ["customer", "channel", "baseline_count", "requested_increase", "priority", "notes"]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 3}),
            "baseline_count": forms.NumberInput(attrs={"min": 0}),
            "requested_increase": forms.NumberInput(attrs={"min": 1}),
        }
        labels = {
            "baseline_count": "Baseline count (current count)",
            "requested_increase": "Requested increase",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.duplicates = []
        self.fields["confirm_duplicate"].widget.attrs["class"] = "checkbox"

        customers = Customer.objects.filter(status=Customer.Status.ACTIVE)
        channels = Channel.objects.filter(status=Channel.Status.ACTIVE)
        if self.instance.pk:
            customers = customers | Customer.objects.filter(pk=self.instance.customer_id)
            channels = channels | Channel.objects.filter(pk=self.instance.channel_id)
        self.fields["customer"].queryset = customers.distinct().order_by("name")
        self.fields["channel"].queryset = channels.distinct().order_by("name")

    def clean(self):
        cleaned = super().clean()
        channel = cleaned.get("channel")
        if channel:
            self.duplicates = list(find_duplicate_orders(channel, self.instance.pk))
            if self.duplicates and not cleaned.get("confirm_duplicate"):
                self.add_error(
                    "confirm_duplicate",
                    "This channel already has an open order. Confirm or cancel.",
                )
        return cleaned


class AssignForm(StyledFormMixin, forms.Form):
    assignee = forms.ModelChoiceField(
        queryset=User.objects.none(), label="Assign to", empty_label="Select staff member"
    )
    reason = forms.CharField(
        required=False,
        label="Reason / note",
        widget=forms.Textarea(attrs={"rows": 2, "placeholder": "Reason / note (optional)"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["assignee"].queryset = User.objects.filter(
            is_active=True, role__in=[User.Role.OPERATOR, User.Role.MANAGER]
        ).order_by("username")


class ObservationForm(StyledFormMixin, forms.Form):
    accuracy = forms.ChoiceField(
        choices=MetricObservation.Accuracy.choices, label="Count type", initial="exact"
    )
    observed_count = forms.IntegerField(min_value=0, required=False, label="Observed count")
    note = forms.CharField(
        required=False, label="Source / note",
        widget=forms.Textarea(attrs={"rows": 2, "placeholder": "Source or note (a reason is required if unavailable)"}),
    )

    def clean(self):
        cleaned = super().clean()
        accuracy = cleaned.get("accuracy")
        count = cleaned.get("observed_count")
        note = (cleaned.get("note") or "").strip()
        cleaned["note"] = note

        if accuracy == MetricObservation.Accuracy.UNAVAILABLE:
            cleaned["observed_count"] = None
            if not note:
                self.add_error("note", "Enter a reason for unavailable (e.g. the count is hidden).")
        elif accuracy and count is None:
            self.add_error("observed_count", "A count is required.")
        return cleaned