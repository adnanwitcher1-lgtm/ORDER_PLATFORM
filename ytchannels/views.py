from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from audit.services import form_changes, log_audit
from accounts.models import User
from accounts.permissions import CUSTOMER_EDIT_ROLES, can_manage_customers, role_required
from customers.models import Customer

from .forms import ChannelForm
from .models import Channel, ChannelInsight


@login_required
def channel_list(request):
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "active")
    customer_id = request.GET.get("customer", "")

    channels = Channel.objects.select_related("customer")
    if status in (Channel.Status.ACTIVE, Channel.Status.ARCHIVED):
        channels = channels.filter(status=status)
    if customer_id.isdigit():
        channels = channels.filter(customer_id=customer_id)
    if q:
        channels = channels.filter(
            Q(name__icontains=q) | Q(url__icontains=q) | Q(customer__name__icontains=q)
        )

    page_obj = Paginator(channels, 10).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)

    return render(request, "channels/channel_list.html", {
        "page_obj": page_obj,
        "q": q,
        "status": status,
        "customer_id": customer_id,
        "customers": Customer.objects.order_by("name"),
        "querystring": params.urlencode(),
        "can_edit": can_manage_customers(request.user),
    })


@role_required(*CUSTOMER_EDIT_ROLES)
def channel_create(request):
    initial = {}
    customer_id = request.GET.get("customer", "")
    if customer_id.isdigit():
        initial["customer"] = customer_id

    form = ChannelForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        channel = form.save(commit=False)
        channel.created_by = request.user
        channel.save()
        log_audit(request, "created", channel)
        messages.success(request, f"Channel '{channel.name}' added.")
        return redirect("channels:list")
    return render(request, "channels/channel_form.html", {
        "form": form,
        "title": "Add Channel",
        "cancel_url": reverse("channels:list"),
        "can_lookup": bool(getattr(settings, "YOUTUBE_API_KEY", "")),
    })


@role_required(*CUSTOMER_EDIT_ROLES)
def channel_update(request, pk):
    channel = get_object_or_404(Channel, pk=pk)
    form = ChannelForm(request.POST or None, instance=channel)
    if request.method == "POST" and form.is_valid():
        changes = form_changes(form)
        form.save()
        if changes:
            log_audit(request, "updated", channel, changes=changes)
        messages.success(request, "Channel updated.")
        return redirect("channels:list")
    return render(request, "channels/channel_form.html", {
        "form": form,
        "title": f"Edit {channel.name}",
        "cancel_url": reverse("channels:list"),
        "can_lookup": bool(getattr(settings, "YOUTUBE_API_KEY", "")),
    })


@role_required(*CUSTOMER_EDIT_ROLES)
@require_POST
def channel_toggle_archive(request, pk):
    channel = get_object_or_404(Channel, pk=pk)
    if channel.status == Channel.Status.ACTIVE:
        channel.status = Channel.Status.ARCHIVED
        action = "archived"
        messages.success(request, f"'{channel.name}' archived.")
    else:
        channel.status = Channel.Status.ACTIVE
        action = "restored"
        messages.success(request, f"'{channel.name}' is active again.")
    channel.save(update_fields=["status", "updated_at"])
    log_audit(request, action, channel)
    return redirect(request.POST.get("next") or "channels:list")


INSIGHT_ROLES = (User.Role.MANAGER, User.Role.SALES, User.Role.OPERATOR)
REFRESH_GAP_SECONDS = 60


def _can_refresh(user):
    return user.is_superuser or user.role in INSIGHT_ROLES


@login_required
def channel_detail(request, pk):
    from . import insights

    channel = get_object_or_404(Channel.objects.select_related("customer"), pk=pk)
    insight = ChannelInsight.objects.filter(channel=channel).first()
    data = insight.data if insight else {}
    return render(request, "channels/channel_detail.html", {
        "channel": channel,
        "insight": insight,
        "data": data,
        "info": data.get("channel", {}),
        "stats": data.get("stats", {}),
        "videos": list(reversed(data.get("videos", [])))[:20],
        "chart_data": insights.chart_payload(data) if data else {},
        "can_refresh": _can_refresh(request.user),
        "analytics": getattr(channel, "analytics", None),
        "can_analytics": bool(channel.google_connection_id),
    })


@login_required
@require_POST
def channel_analytics_refresh(request, pk):
    from django.core.exceptions import PermissionDenied
    from . import analytics
    from .insights import InsightError
    from .models import ChannelAnalytics

    if not _can_refresh(request.user):
        raise PermissionDenied
    channel = get_object_or_404(Channel.objects.select_related("google_connection"), pk=pk)
    existing = ChannelAnalytics.objects.filter(channel=channel).first()
    if existing and (timezone.now() - existing.fetched_at).total_seconds() < REFRESH_GAP_SECONDS:
        messages.info(request, "Analytics was just refreshed. Please wait a minute.")
        return redirect("channels:detail", pk=pk)
    order = channel.orders.order_by("-baseline_recorded_at").first() if hasattr(channel, "orders") else None
    since = timezone.localtime(order.baseline_recorded_at).date() if order and order.baseline_recorded_at else None
    try:
        data = analytics.fetch(channel, since=since)
    except InsightError as exc:
        messages.error(request, str(exc))
        return redirect("channels:detail", pk=pk)
    ChannelAnalytics.objects.update_or_create(
        channel=channel, defaults={"data": data, "fetched_at": timezone.now(), "fetched_by": request.user})
    messages.success(request, "Analytics refreshed (this data is 1-3 days behind YouTube).")
    return redirect("channels:detail", pk=pk)


@login_required
@require_POST
def channel_insights_refresh(request, pk):
    from django.core.exceptions import PermissionDenied
    from . import insights

    if not _can_refresh(request.user):
        raise PermissionDenied
    channel = get_object_or_404(Channel, pk=pk)
    existing = ChannelInsight.objects.filter(channel=channel).first()
    if existing and (timezone.now() - existing.fetched_at).total_seconds() < REFRESH_GAP_SECONDS:
        messages.info(request, "The details were just refreshed. Please wait a minute before refreshing again.")
        return redirect("channels:detail", pk=pk)
    try:
        data = insights.fetch(channel)
    except insights.InsightError as exc:
        messages.error(request, str(exc))
        return redirect("channels:detail", pk=pk)
    ChannelInsight.objects.update_or_create(
        channel=channel,
        defaults={"data": data, "fetched_at": timezone.now(), "fetched_by": request.user},
    )
    messages.success(request, "Channel details refreshed from YouTube.")
    return redirect("channels:detail", pk=pk)


@login_required
@require_POST
def channel_insights_ai(request, pk):
    from django.core.exceptions import PermissionDenied
    from . import insights

    if not _can_refresh(request.user):
        raise PermissionDenied
    channel = get_object_or_404(Channel, pk=pk)
    insight = ChannelInsight.objects.filter(channel=channel).first()
    if not insight:
        messages.error(request, "Fetch the channel details first.")
        return redirect("channels:detail", pk=pk)
    lang = "ur" if request.POST.get("lang") == "ur" else "en"
    text, source, note = insights.summarize(insight.data, lang, request.user)
    insight.ai_summary, insight.ai_source, insight.ai_lang, insight.ai_at = text, source, lang, timezone.now()
    insight.save(update_fields=["ai_summary", "ai_source", "ai_lang", "ai_at"])
    if note:
        messages.warning(request, note)
    return redirect("channels:detail", pk=pk)


@role_required(*CUSTOMER_EDIT_ROLES)
def channel_lookup(request):
    """JSON: details of a YouTube link / @handle / channel ID (used by the Add Channel form)."""
    from . import resolver
    from .insights import InsightError

    try:
        preview = resolver.describe(request.GET.get("q", ""))
    except InsightError as exc:
        return JsonResponse({"ok": False, "error": str(exc)})
    existing = Channel.objects.filter(youtube_channel_id=preview["id"]).exclude(
        pk=request.GET.get("exclude") or 0).first()
    return JsonResponse({
        "ok": True, "channel": preview,
        "existing": existing.name if existing else "",
    })


@login_required
def channel_current_count(request, pk):
    """JSON: the channel's current public subscriber count (used to fill the order baseline)."""
    from orders.youtube import fetch_subscriber_count

    channel = get_object_or_404(Channel, pk=pk)
    result = fetch_subscriber_count(channel)
    return JsonResponse({
        "ok": result.api_status == "ok",
        "count": result.count,
        "accuracy": result.accuracy,
        "message": result.message,
        "status": result.api_status,
    })
