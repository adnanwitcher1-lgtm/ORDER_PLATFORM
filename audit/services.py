import ipaddress

from django import forms
from django.conf import settings
from django.db import models

from .models import AuditLog

# Fields with these names are never logged; only "(changed)" is recorded.
REDACT_PARTS = ("password", "notes", "token", "secret")


def get_client_ip(request):
    if request is None:
        return None
    raw = request.META.get("REMOTE_ADDR", "")
    if getattr(settings, "TRUST_PROXY_HEADERS", False):
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            raw = forwarded.split(",")[0].strip()
    try:
        return str(ipaddress.ip_address(raw))
    except ValueError:
        return None


def _label(form, name, value):
    field = form.fields[name]
    if isinstance(field, forms.ModelChoiceField):
        if value in (None, ""):
            return ""
        if isinstance(value, models.Model):
            return str(value)
        obj = field.queryset.filter(pk=value).first()
        return str(obj) if obj else str(value)
    return "" if value is None else str(value)[:120]


def form_changes(form):
    """{field: [old value, new value]}. Private fields show only '(changed)'."""
    changes = {}
    for name in form.changed_data:
        field = form.fields.get(name)
        if field is None:
            continue
        if any(part in name for part in REDACT_PARTS) or isinstance(field, forms.ModelMultipleChoiceField):
            changes[name] = "(changed)"
            continue
        changes[name] = [
            _label(form, name, form.initial.get(name)),
            _label(form, name, form.cleaned_data.get(name)),
        ]
    return changes


def log_audit(request, action, obj=None, *, entity_type="", entity_label="",
              entity_id="", changes=None, actor=None):
    user = actor
    if user is None and request is not None:
        candidate = getattr(request, "user", None)
        if candidate is not None and candidate.is_authenticated:
            user = candidate

    return AuditLog.objects.create(
        actor=user,
        action=action,
        entity_type=entity_type or (obj.__class__.__name__ if obj is not None else ""),
        entity_id=str(obj.pk) if obj is not None and obj.pk is not None else entity_id,
        entity_label=(entity_label or (str(obj) if obj is not None else ""))[:200],
        changes=changes or {},
        ip_address=get_client_ip(request),
        user_agent=(request.META.get("HTTP_USER_AGENT", "")[:200] if request is not None else ""),
    )