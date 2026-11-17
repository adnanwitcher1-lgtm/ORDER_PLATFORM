from datetime import timedelta

from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone

from audit.services import get_client_ip

from .models import UserSession
from .twofactor import has_two_factor

TOUCH_EVERY = timedelta(minutes=5)


class SessionTrackingMiddleware:
    """Keeps UserSession rows up to date (used by the 'Your sessions' page)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        user = getattr(request, "user", None)
        key = getattr(getattr(request, "session", None), "session_key", None)
        if user is not None and user.is_authenticated and key:
            now = timezone.now()
            row = UserSession.objects.filter(session_key=key).first()
            if row is None:
                UserSession.objects.create(
                    user=user, session_key=key, ip_address=get_client_ip(request),
                    user_agent=request.META.get("HTTP_USER_AGENT", "")[:200],
                )
            elif now - row.last_seen > TOUCH_EVERY:
                UserSession.objects.filter(pk=row.pk).update(last_seen=now, ip_address=get_client_ip(request))
        return response


class TwoFactorEnforcementMiddleware:
    """When REQUIRE_2FA is on, users without an authenticator app must set one up first."""

    EXEMPT_PREFIXES = ("/static/", "/accounts/logout/", "/accounts/security/2fa/", "/accounts/login/")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if getattr(settings, "REQUIRE_2FA", False):
            user = getattr(request, "user", None)
            if (
                user is not None and user.is_authenticated
                and not request.path.startswith(self.EXEMPT_PREFIXES)
                and not request.path.startswith("/portal/")
                and not has_two_factor(user)
            ):
                return redirect("accounts:two_factor_setup")
        return self.get_response(request)
