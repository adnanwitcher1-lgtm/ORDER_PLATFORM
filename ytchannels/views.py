from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from audit.services import form_changes, log_audit
from accounts.permissions import CUSTOMER_EDIT_ROLES, can_manage_customers, role_required
from customers.models import Customer

from .forms import ChannelForm
from .models import Channel


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
    return render(request, "form_page.html", {
        "form": form,
        "title": "Add Channel",
        "cancel_url": reverse("channels:list"),
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
    return render(request, "form_page.html", {
        "form": form,
        "title": f"Edit {channel.name}",
        "cancel_url": reverse("channels:list"),
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