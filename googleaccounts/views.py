import hmac
import secrets

from django.contrib import messages
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from accounts.permissions import (
    GOOGLE_MANAGE_ROLES,
    GOOGLE_VIEW_ROLES,
    can_manage_google,
    role_required,
)

from . import oauth, services
from .crypto import TokenCryptoError
from .forms import ConnectForm, ConnectionEditForm
from .models import ConnectionEvent, GoogleAccountConnection

S = GoogleAccountConnection.Status


def _start_flow(request, pending, login_hint=""):
    state = secrets.token_urlsafe(32)
    request.session["google_oauth_state"] = state
    request.session["google_oauth_pending"] = pending
    return redirect(oauth.build_auth_url(state, login_hint))


@role_required(*GOOGLE_VIEW_ROLES)
def connection_list(request):
    status = request.GET.get("status", "")
    connections = GoogleAccountConnection.objects.select_related("connected_by")
    if status in S.values:
        connections = connections.filter(status=status)

    page_obj = Paginator(connections, 25).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)

    everything = GoogleAccountConnection.objects
    return render(request, "google/connection_list.html", {
        "page_obj": page_obj,
        "status": status,
        "status_choices": S.choices,
        "querystring": params.urlencode(),
        "connected_count": everything.filter(status=S.CONNECTED).count(),
        "reauth_count": everything.filter(status=S.NEEDS_REAUTH).count(),
        "can_manage": can_manage_google(request.user),
        "configured": oauth.is_configured(),
    })


@role_required(*GOOGLE_VIEW_ROLES)
def connection_detail(request, pk):
    connection = get_object_or_404(
        GoogleAccountConnection.objects.select_related("connected_by", "disconnected_by"), pk=pk
    )
    from customers.models import Customer
    from ytchannels.models import Channel

    channels, channels_error = [], ""
    if connection.status == S.CONNECTED:
        try:
            channels = services.list_channels(connection)
        except services.ServiceError as exc:
            channels_error = str(exc)
    existing = {
        c.youtube_channel_id: c
        for c in Channel.objects.filter(youtube_channel_id__in=[ch["id"] for ch in channels])
    }
    for ch in channels:
        ch["existing"] = existing.get(ch["id"])
    return render(request, "google/connection_detail.html", {
        "connection": connection,
        "events": connection.events.select_related("actor")[:50],
        "can_manage": can_manage_google(request.user),
        "channels": channels,
        "channels_error": channels_error,
        "customers": Customer.objects.filter(status=Customer.Status.ACTIVE).order_by("name"),
    })


@role_required(*GOOGLE_MANAGE_ROLES)
@require_POST
def connection_import_channel(request, pk):
    """Add one of the account's own channels to a customer (one click)."""
    from audit.services import log_audit
    from customers.models import Customer
    from ytchannels.models import Channel

    connection = get_object_or_404(GoogleAccountConnection, pk=pk)
    back = redirect("google:detail", pk=pk)
    customer = Customer.objects.filter(pk=request.POST.get("customer") or 0, status=Customer.Status.ACTIVE).first()
    if not customer:
        messages.error(request, "Choose a customer first.")
        return back
    try:
        channels = services.list_channels(connection)
    except services.ServiceError as exc:
        messages.error(request, str(exc))
        return back
    # Only a channel the account really returned can be imported (the id is not trusted from the form).
    chosen = next((c for c in channels if c["id"] == request.POST.get("channel_id")), None)
    if not chosen:
        messages.error(request, "That channel is not available on this Google account.")
        return back
    clash = Channel.objects.filter(youtube_channel_id=chosen["id"]).first() or \
        Channel.objects.filter(url=chosen["url"]).first()
    if clash:
        messages.error(request, f"This channel is already added as '{clash.name}'.")
        return back
    channel = Channel.objects.create(
        customer=customer, name=chosen["title"] or chosen["id"], url=chosen["url"],
        youtube_channel_id=chosen["id"], google_connection=connection, created_by=request.user,
    )
    log_audit(request, "created", channel)
    messages.success(request, f"'{channel.name}' added to {customer.name}.")
    return back


@role_required(*GOOGLE_MANAGE_ROLES)
def connect_start(request):
    if not oauth.is_configured():
        messages.error(request, "Google OAuth is not configured yet (client id, secret and encryption key in .env).")
        return redirect("google:list")

    form = ConnectForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        return _start_flow(request, {
            "label": form.cleaned_data["label"],
            "notes": form.cleaned_data["notes"],
        })
    return render(request, "form_page.html", {
        "form": form,
        "title": "Connect Google Account",
        "cancel_url": reverse("google:list"),
        "submit_label": "Continue to Google",
    })


@role_required(*GOOGLE_MANAGE_ROLES)
def oauth_callback(request):
    expected = request.session.pop("google_oauth_state", None)
    pending = request.session.pop("google_oauth_pending", None)
    state = request.GET.get("state", "")

    if not expected or not pending or not hmac.compare_digest(expected, state):
        messages.error(request, "The request was invalid or the session expired. Please try again.")
        return redirect("google:list")
    if request.GET.get("error") or not request.GET.get("code"):
        messages.error(request, "Access was not granted on Google, so the account was not connected.")
        return redirect("google:list")

    try:
        connection, action = services.complete_connection(
            request.GET["code"], pending, request.user
        )
    except (oauth.OAuthError, services.ServiceError, TokenCryptoError) as exc:
        messages.error(request, str(exc))
        return redirect("google:list")

    messages.success(request, f"{connection.google_email}: {action} completed.")
    return redirect("google:detail", pk=connection.pk)


@role_required(*GOOGLE_MANAGE_ROLES)
@require_POST
def connection_reauthorize(request, pk):
    connection = get_object_or_404(GoogleAccountConnection, pk=pk)
    if not oauth.is_configured():
        messages.error(request, "Google OAuth is not configured.")
        return redirect("google:list")
    return _start_flow(
        request,
        {"label": connection.label, "notes": "", "reauth_pk": connection.pk},
        login_hint=connection.google_email,
    )


@role_required(*GOOGLE_MANAGE_ROLES)
@require_POST
def connection_check(request, pk):
    connection = get_object_or_404(GoogleAccountConnection, pk=pk)
    try:
        result = services.check_connection(connection, request.user)
    except (services.ServiceError, TokenCryptoError) as exc:
        messages.error(request, str(exc))
    else:
        if result.ok:
            messages.success(request, f"{connection.label}: authorization is valid.")
        elif result.needs_reauth:
            messages.warning(request, f"{connection.label}: needs reauthorization. Please reconnect.")
        else:
            messages.error(request, f"{connection.label}: check failed. {result.message}")
    return redirect(request.POST.get("next") or "google:list")


@role_required(*GOOGLE_MANAGE_ROLES)
@require_POST
def connection_disconnect(request, pk):
    connection = get_object_or_404(GoogleAccountConnection, pk=pk)
    if connection.status == S.DISCONNECTED:
        messages.error(request, "This account is already disconnected.")
        return redirect("google:list")
    revoked = services.disconnect_connection(connection, request.user)
    if revoked:
        messages.success(request, f"{connection.label} disconnected and the token was revoked at Google.")
    else:
        messages.warning(
            request,
            f"{connection.label} disconnected and the local token was deleted. Revocation at Google was not confirmed. "
            "Remove the app's access at myaccount.google.com/permissions.",
        )
    return redirect("google:list")


@role_required(*GOOGLE_MANAGE_ROLES)
def connection_edit(request, pk):
    connection = get_object_or_404(GoogleAccountConnection, pk=pk)
    form = ConnectionEditForm(request.POST or None, instance=connection)
    if request.method == "POST" and form.is_valid():
        form.save()
        services.log_event(connection, request.user, "Details edited", f"Label: {connection.label}")
        messages.success(request, "Account details updated.")
        return redirect("google:detail", pk=connection.pk)
    return render(request, "form_page.html", {
        "form": form,
        "title": f"Edit {connection.label}",
        "cancel_url": reverse("google:detail", args=[connection.pk]),
    })