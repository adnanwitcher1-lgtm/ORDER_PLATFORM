import time

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, PasswordChangeView
from django.contrib.sessions.models import Session
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.safestring import mark_safe
from django.views.decorators.http import require_POST
from django.conf import settings

from audit.services import get_client_ip, log_audit

from . import ratelimit, twofactor
from .forms import StaffLoginForm
from .models import User, UserSession

PENDING_KEY = "pending_2fa"
SETUP_KEY = "setup_2fa_secret"
PENDING_SECONDS = 5 * 60


class StaffLoginView(LoginView):
    template_name = "accounts/login.html"
    authentication_form = StaffLoginForm
    redirect_authenticated_user = True

    def post(self, request, *args, **kwargs):
        username = request.POST.get("username", "")
        if ratelimit.is_locked(username, get_client_ip(request)):
            response = self.render_to_response(self.get_context_data(form=self.get_form(), locked=True))
            response.status_code = 429
            return response
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        user = form.get_user()
        if twofactor.has_two_factor(user):
            # Password is correct, but the person is not signed in until the code is checked.
            self.request.session[PENDING_KEY] = {
                "user_id": user.pk,
                "backend": user.backend,
                "next": self.get_success_url(),
                "at": time.time(),
            }
            return redirect("accounts:two_factor_verify")
        return super().form_valid(form)


def two_factor_verify(request):
    pending = request.session.get(PENDING_KEY)
    if not pending or time.time() - pending.get("at", 0) > PENDING_SECONDS:
        request.session.pop(PENDING_KEY, None)
        messages.error(request, "Your sign-in expired. Please sign in again.")
        return redirect("accounts:login")

    user = get_object_or_404(User, pk=pending["user_id"], is_active=True)
    ip = get_client_ip(request)
    locked = ratelimit.is_locked(user.username, ip)
    error = ""

    if request.method == "POST":
        if locked:
            error = "Too many failed attempts. Please try again after 15 minutes."
        else:
            code = request.POST.get("code", "")
            used_recovery = False
            ok = twofactor.verify_code(user, code)
            if not ok and twofactor.use_recovery_code(user, code):
                ok = used_recovery = True
            if ok:
                request.session.pop(PENDING_KEY, None)
                login(request, user, backend=pending["backend"])
                if used_recovery:
                    left = twofactor.unused_recovery_count(user)
                    messages.warning(request, f"You signed in with a recovery code. {left} left. Create new codes in Security.")
                return redirect(pending.get("next") or settings.LOGIN_REDIRECT_URL)
            ratelimit.register_failure(user.username, ip)
            log_audit(request, "2FA failed", user, entity_type="Login", entity_label=user.username, actor=user)
            error = "That code is not correct. Try the newest code from your authenticator app."

    response = render(request, "accounts/verify_2fa.html", {"error": error, "locked": locked})
    if locked or (error and request.method == "POST" and ratelimit.is_locked(user.username, ip)):
        response.status_code = 429
    return response


@login_required
def security_overview(request):
    # Drop rows whose Django session no longer exists (logged out / expired).
    rows = list(UserSession.objects.filter(user=request.user))
    alive = set(
        Session.objects.filter(
            session_key__in=[r.session_key for r in rows], expire_date__gt=timezone.now()
        ).values_list("session_key", flat=True)
    )
    stale = [r.pk for r in rows if r.session_key not in alive]
    if stale:
        UserSession.objects.filter(pk__in=stale).delete()
    sessions = [r for r in rows if r.pk not in stale]
    current = request.session.session_key
    return render(request, "accounts/security.html", {
        "has_2fa": twofactor.has_two_factor(request.user),
        "recovery_left": twofactor.unused_recovery_count(request.user),
        "sessions": sessions,
        "current_key": current,
        "require_2fa": getattr(settings, "REQUIRE_2FA", False),
    })


@login_required
def two_factor_setup(request):
    if twofactor.has_two_factor(request.user):
        messages.info(request, "Two-factor authentication is already on.")
        return redirect("accounts:security")

    secret = request.session.get(SETUP_KEY)
    if not secret:
        secret = twofactor.new_secret()
        request.session[SETUP_KEY] = secret

    error = ""
    if request.method == "POST":
        codes = twofactor.confirm_setup(request.user, secret, request.POST.get("code", ""))
        if codes:
            request.session.pop(SETUP_KEY, None)
            log_audit(request, "2FA enabled", request.user, entity_type="Login", entity_label=request.user.username)
            return render(request, "accounts/recovery_codes.html", {"codes": codes, "just_enabled": True})
        error = "That code is not correct. Check the time on your phone and try again."

    uri = twofactor.provisioning_uri(request.user, secret)
    return render(request, "accounts/two_factor_setup.html", {
        "qr_svg": mark_safe(twofactor.qr_svg(uri)),   # generated by us from the secret, not user input
        "secret": secret,
        "error": error,
    })


def _confirmed_identity(request):
    """Password + a current code (or recovery code) are required for sensitive changes."""
    password = request.POST.get("password", "")
    code = request.POST.get("code", "")
    if not request.user.check_password(password):
        return False
    return twofactor.verify_code(request.user, code) or twofactor.use_recovery_code(request.user, code)


@login_required
@require_POST
def two_factor_disable(request):
    if getattr(settings, "REQUIRE_2FA", False):
        messages.error(request, "Two-factor authentication is required for all users and cannot be turned off.")
    elif not _confirmed_identity(request):
        messages.error(request, "Password or code is not correct. Two-factor authentication stays on.")
    else:
        twofactor.disable(request.user)
        log_audit(request, "2FA disabled", request.user, entity_type="Login", entity_label=request.user.username)
        messages.success(request, "Two-factor authentication turned off.")
    return redirect("accounts:security")


@login_required
@require_POST
def recovery_regenerate(request):
    if not twofactor.has_two_factor(request.user):
        return redirect("accounts:security")
    if not _confirmed_identity(request):
        messages.error(request, "Password or code is not correct.")
        return redirect("accounts:security")
    codes = twofactor.regenerate_recovery_codes(request.user)
    log_audit(request, "2FA recovery codes regenerated", request.user, entity_type="Login",
              entity_label=request.user.username)
    return render(request, "accounts/recovery_codes.html", {"codes": codes, "just_enabled": False})


@login_required
@require_POST
def session_revoke(request, pk):
    row = get_object_or_404(UserSession, pk=pk, user=request.user)
    if row.session_key == request.session.session_key:
        messages.info(request, "Use Logout to end this session.")
        return redirect("accounts:security")
    Session.objects.filter(session_key=row.session_key).delete()
    row.delete()
    log_audit(request, "session ended", request.user, entity_type="Login", entity_label=request.user.username)
    messages.success(request, "That session was signed out.")
    return redirect("accounts:security")


@login_required
@require_POST
def session_revoke_others(request):
    others = UserSession.objects.filter(user=request.user).exclude(session_key=request.session.session_key)
    keys = list(others.values_list("session_key", flat=True))
    Session.objects.filter(session_key__in=keys).delete()
    count = others.delete()[0]
    log_audit(request, "other sessions ended", request.user, entity_type="Login",
              entity_label=request.user.username)
    messages.success(request, f"Signed out {count} other session{'s' if count != 1 else ''}.")
    return redirect("accounts:security")


class StaffPasswordChangeView(PasswordChangeView):
    template_name = "form_page.html"
    success_url = reverse_lazy("accounts:security")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({"title": "Change Password", "cancel_url": reverse("accounts:security")})
        return context

    def form_valid(self, form):
        response = super().form_valid(form)   # keeps this session signed in, ends all the others
        log_audit(self.request, "password changed", self.request.user, entity_type="Login",
                  entity_label=self.request.user.username)
        messages.success(self.request, "Password changed. Other sessions were signed out.")
        return response
