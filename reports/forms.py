from django import forms

from accounts.models import User
from core.forms import StyledFormMixin
from customers.models import Customer


class ReportFilterForm(StyledFormMixin, forms.Form):
    date_from = forms.DateField(required=False, label="From", widget=forms.DateInput(attrs={"type": "date"}))
    date_to = forms.DateField(required=False, label="To", widget=forms.DateInput(attrs={"type": "date"}))
    customer = forms.ModelChoiceField(
        queryset=Customer.objects.order_by("name"), required=False, empty_label="All customers"
    )
    status = forms.ChoiceField(required=False)
    staff = forms.ModelChoiceField(
        queryset=User.objects.filter(is_active=True).order_by("username"),
        required=False, empty_label="All staff", label="Staff",
    )

    def __init__(self, *args, filters=(), status_choices=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["status"].choices = [("", "All statuses"), *status_choices]
        for name in list(self.fields):
            if name not in filters:
                del self.fields[name]