from urllib.parse import urlparse

from django import forms
from django.conf import settings

from core.forms import StyledFormMixin
from customers.models import Customer

from . import resolver
from .insights import InsightError
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
        self.fields["name"].required = False
        self.fields["name"].help_text = "Leave empty to take the name from YouTube."
        self.fields["youtube_channel_id"].help_text = "Filled in automatically from the link when possible."
        self.preview = None

    def clean_url(self):
        url = self.cleaned_data["url"].strip().rstrip("/")
        host = urlparse(url).netloc.lower()
        if host not in YOUTUBE_HOSTS:
            raise forms.ValidationError("Only YouTube channel URLs are allowed (youtube.com).")
        return url

    def clean(self):
        cleaned = super().clean()
        url = cleaned.get("url")
        cid = (cleaned.get("youtube_channel_id") or "").strip()
        name = (cleaned.get("name") or "").strip()

        if url and not self.errors.get("url") and getattr(settings, "AUTO_LOOKUP", False) \
                and getattr(settings, "YOUTUBE_API_KEY", "") and (not cid or not name):
            try:
                self.preview = resolver.describe(cid or url)
            except InsightError:
                self.preview = None      # not fatal: the staff member can type the details
        if self.preview:
            cid = cid or self.preview["id"]
            name = name or self.preview["title"]
            kind = resolver.parse_reference(url)
            if kind and kind[0] == "video":          # a video link was pasted: keep the channel link instead
                cleaned["url"] = self.preview["url"]
        cleaned["youtube_channel_id"] = cid
        cleaned["name"] = name
        if not name:
            self.add_error("name", "Enter a name (it could not be read from YouTube).")
        if cid:
            other = Channel.objects.filter(youtube_channel_id=cid).exclude(pk=self.instance.pk).first()
            if other:
                self.add_error("youtube_channel_id", f"This YouTube channel is already added as '{other.name}'.")
        return cleaned
