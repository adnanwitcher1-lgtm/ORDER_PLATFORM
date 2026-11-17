from urllib.parse import urlparse

from django import forms

from core.forms import StyledFormMixin
from customers.models import Customer

from .models import Channel

YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}


class ChannelForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Channel
        fields = ["customer", "name", "url", "youtube_channel_id", "authorization_notes"]
        widgets = {"authorization_notes": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        customers = Customer.objects.filter(status=Customer.Status.ACTIVE)
        if self.instance.pk:
            customers = customers | Customer.objects.filter(pk=self.instance.customer_id)
        self.fields["customer"].queryset = customers.order_by("name")

    def clean_url(self):
        url = self.cleaned_data["url"].strip().rstrip("/")
        host = urlparse(url).netloc.lower()
        if host not in YOUTUBE_HOSTS:
            raise forms.ValidationError("Only YouTube channel URLs are allowed (youtube.com).")
        return url