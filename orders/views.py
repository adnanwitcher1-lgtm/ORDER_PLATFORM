from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.permissions import ORDER_EDIT_ROLES, can_manage_orders, role_required
from ytchannels.models import Channel

from .insights import apply_search, parse_search
from .forecast import chart_payload, estimate_eta
from .forms import AssignForm, ObservationForm, OrderForm
from .models import MetricObservation, Order, OrderEvent
from .observations import (
    ObservationError,
    can_fetch_observation,
    can_record_observation,
    record_observation,
)
from .utils import find_duplicate_orders
from .workflow import (
    WorkflowError,
    apply_transition,
    assign_order,
    available_transitions,
    can_assign,
)
from .youtube import fetch_subscriber_count


@login_required
def order_list(request):
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "")
    priority = request.GET.get("priority", "")

    orders = Order.objects.select_related("customer", "channel", "assigned_to")
    ask = request.GET.get("ask", "").strip()[:200]
    parsed = None
    if ask:
        parsed = parse_search(ask, request.user)
        if parsed.understood:
            orders = apply_search(orders, parsed, request.user)
    if status in Order.Status.values:
        orders = orders.filter(status=status)
    if priority in Order.Priority.values:
        orders = orders.filter(priority=priority)
    if q:
        cond = (
            Q(customer__name__icontains=q)
            | Q(channel__name__icontains=q)
            | Q(channel__url__icontains=q)
        )
        number = q.lower().replace("ord-", "").lstrip("0")
        if number.isdigit():
            cond |= Q(pk=int(number))
        orders = orders.filter(cond)

    page_obj = Paginator(orders, 10).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)

    return render(request, "orders/order_list.html", {
        "page_obj": page_obj,
        "q": q,
        "ask": ask,
        "ask_chips": parsed.chips() if parsed else [],
        "ask_failed": bool(parsed and not parsed.understood),
        "ask_ai": bool(parsed and parsed.used_ai),
        "status": status,
        "priority": priority,
        "status_choices": Order.Status.choices,
        "priority_choices": Order.Priority.choices,
        "querystring": params.urlencode(),
        "can_edit": can_manage_orders(request.user),
    })


@login_required
def order_detail(request, pk):
    order = get_object_or_404(
        Order.objects.select_related(
            "customer", "channel", "assigned_to", "created_by", "reviewed_by"
        ),
        pk=pk,
    )
    return render(request, "orders/order_detail.html", {
        "order": order,
        "p": order.progress,
        "duplicates": find_duplicate_orders(order.channel, order.pk),
        "can_edit": can_manage_orders(request.user),
        "is_editable": order.status in Order.EDITABLE_STATUSES,
        "transitions": available_transitions(request.user, order),
        "can_assign": can_assign(request.user, order),
        "assign_form": AssignForm(initial={"assignee": order.assigned_to_id}),
        "assignments": order.assignments.select_related("assigned_to", "changed_by"),
        "events": order.events.select_related("actor"),
        "observations": order.observations.select_related("recorded_by")[:20],
        "can_record": can_record_observation(request.user, order),
        "can_fetch": can_fetch_observation(request.user, order),
        "observation_form": ObservationForm(),
        "eta": estimate_eta(order),
        "chart_data": chart_payload(order),
    })


@role_required(*ORDER_EDIT_ROLES)
def order_create(request):
    initial = {}
    channel_id = request.GET.get("channel", "")
    if channel_id.isdigit():
        channel = Channel.objects.filter(pk=channel_id).first()
        if channel:
            initial = {"customer": channel.customer_id, "channel": channel.pk}

    form = OrderForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        order = form.save(commit=False)
        order.created_by = request.user
        order.save()
        OrderEvent.objects.create(
            order=order, actor=request.user, action="Order created",
            to_status=order.status,
            note=f"Baseline {order.baseline_count}, increase {order.requested_increase}, target {order.target_count}",
        )
        messages.success(request, f"{order.order_number} draft created. Target: {order.target_count}")
        return redirect("orders:detail", pk=order.pk)
    return render(request, "orders/order_form.html", {"form": form, "title": "New Order"})


@role_required(*ORDER_EDIT_ROLES)
def order_update(request, pk):
    order = get_object_or_404(Order, pk=pk)
    if order.status not in Order.EDITABLE_STATUSES:
        messages.error(request, "An approved order cannot be edited. Ask a manager to make a correction.")
        return redirect("orders:detail", pk=order.pk)

    form = OrderForm(request.POST or None, instance=order)
    if request.method == "POST" and form.is_valid():
        order = form.save()
        OrderEvent.objects.create(
            order=order, actor=request.user, action="Order edited",
            from_status=order.status, to_status=order.status,
            note=f"Baseline {order.baseline_count}, increase {order.requested_increase}, target {order.target_count}",
        )
        messages.success(request, f"{order.order_number} updated. Target: {order.target_count}")
        return redirect("orders:detail", pk=order.pk)
    return render(request, "orders/order_form.html", {
        "form": form,
        "title": f"Edit {order.order_number}",
        "order": order,
    })


@login_required
@require_POST
def order_action(request, pk, key):
    order = get_object_or_404(Order, pk=pk)
    try:
        order = apply_transition(order, key, request.user, request.POST.get("reason", ""))
    except WorkflowError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"{order.order_number}: status is now '{order.get_status_display()}'.")
    return redirect("orders:detail", pk=pk)


@login_required
@require_POST
def order_assign(request, pk):
    order = get_object_or_404(Order, pk=pk)
    form = AssignForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Select a staff member.")
        return redirect("orders:detail", pk=pk)
    try:
        order = assign_order(
            order, form.cleaned_data["assignee"], request.user, form.cleaned_data["reason"]
        )
    except WorkflowError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"{order.order_number} is now assigned to {order.assigned_to.display_name}.")
    return redirect("orders:detail", pk=pk)


@login_required
def customer_channels(request, customer_id):
    channels = Channel.objects.filter(
        customer_id=customer_id, status=Channel.Status.ACTIVE
    ).order_by("name")
    return JsonResponse({"channels": [{"id": c.pk, "name": c.name} for c in channels]})


@login_required
@require_POST
def observation_create(request, pk):
    order = get_object_or_404(Order, pk=pk)
    if not can_record_observation(request.user, order):
        raise PermissionDenied

    form = ObservationForm(request.POST)
    if not form.is_valid():
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)
        return redirect("orders:detail", pk=pk)

    data = form.cleaned_data
    try:
        _, order = record_observation(
            order,
            count=data["observed_count"],
            accuracy=data["accuracy"],
            source=MetricObservation.Source.MANUAL,
            user=request.user,
            note=data["note"],
        )
    except ObservationError as exc:
        messages.error(request, str(exc))
    else:
        if order.status == Order.Status.TARGET_OBSERVED:
            messages.success(request, "Observation saved. Target observed, review is now required.")
        else:
            messages.success(request, "Observation saved.")
    return redirect("orders:detail", pk=pk)


@login_required
@require_POST
def observation_fetch(request, pk):
    order = get_object_or_404(Order.objects.select_related("channel"), pk=pk)
    if not can_fetch_observation(request.user, order):
        raise PermissionDenied

    result = fetch_subscriber_count(order.channel)

    # Setup problems (key/URL) are not recorded in history; only a message is shown.
    if result.api_status in ("not_configured", "unsupported_url"):
        messages.error(request, result.message)
        return redirect("orders:detail", pk=pk)

    try:
        _, order = record_observation(
            order,
            count=result.count,
            accuracy=result.accuracy,
            source=MetricObservation.Source.YOUTUBE_API,
            user=request.user,
            note=result.message,
            api_status=result.api_status,
        )
    except ObservationError as exc:
        messages.error(request, str(exc))
    else:
        if result.count is None:
            messages.warning(request, f"Count not found: {result.message}")
        elif order.status == Order.Status.TARGET_OBSERVED:
            messages.success(request, f"YouTube count: {result.count}. Target observed, review is now required.")
        else:
            messages.success(request, f"YouTube count: {result.count} ({result.accuracy}).")
    return redirect("orders:detail", pk=pk)