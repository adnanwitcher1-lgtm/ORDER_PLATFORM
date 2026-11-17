from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

from audit.services import get_client_ip, log_audit

from . import ratelimit


@receiver(user_logged_in)
def on_login(sender, request, user, **kwargs):
    log_audit(request, "Login", user, entity_type="Login", entity_label=user.username, actor=user)
    ratelimit.clear(user.username, get_client_ip(request))


@receiver(user_logged_out)
def on_logout(sender, request, user, **kwargs):
    key = getattr(getattr(request, "session", None), "session_key", None)
    if key:
        from .models import UserSession
        UserSession.objects.filter(session_key=key).delete()
    if user is not None:
        log_audit(request, "Logout", user, entity_type="Login", entity_label=user.username, actor=user)


@receiver(user_login_failed)
def on_login_failed(sender, credentials, request=None, **kwargs):
    # Only the username, never the password
    username = (credentials or {}).get("username", "") or ""
    ratelimit.register_failure(username, get_client_ip(request))
    log_audit(request, "Login failed", entity_type="Login", entity_label=username[:150])