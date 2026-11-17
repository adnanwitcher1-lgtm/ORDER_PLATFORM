from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from audit.services import form_changes, log_audit
from accounts.permissions import CUSTOMER_EDIT_ROLES, can_manage_customers, role_required

from reports.client_report import client_orders, render_pdf
from reports import whatsapp
from reports.delivery import DeliveryError, portal_url_for, send_customer_report

from .forms import CustomerForm
from .models import Customer


@login_required
def customer_list(request):
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "active")

    customers = Customer.objects.annotate(channel_count=Count("channels"))
    if status in (Customer.Status.ACTIVE, Customer.Status.ARCHIVED):
        customers = customers.filter(status=status)
    if q:
        customers = customers.filter(
            Q(name__icontains=q) | Q(email__icontains=q) | Q(phone__icontains=q)
        )

    page_obj = Paginator(customers, 10).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)

    return render(request, "customers/customer_list.html", {
        "page_obj": page_obj,
        "q": q,
        "status": status,
        "querystring": params.urlencode(),
        "can_edit": can_manage_customers(request.user),
    })


@login_required
def customer_detail(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    portal_url = portal_url_for(customer, request)
    whatsapp_link = ""
    if portal_url:
        whatsapp_link = customer.whatsapp_link(
            f"Hello {customer.name}, you can follow your order progress live here: {portal_url}"
        )
    return render(request, "customers/customer_detail.html", {
        "customer": customer,
        "channels": customer.channels.all(),
        "can_edit": can_manage_customers(request.user),
        "portal_url": portal_url,
        "whatsapp_url": whatsapp_link,
        "whatsapp_api": whatsapp.is_configured(),
    })


@role_required(*CUSTOMER_EDIT_ROLES)
def customer_create(request):
    form = CustomerForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        customer = form.save(commit=False)
        customer.created_by = request.user
        customer.save()
        log_audit(request, "created", customer)
        messages.success(request, f"Customer '{customer.name}' added.")
        return redirect("customers:detail", pk=customer.pk)
    return render(request, "form_page.html", {
        "form": form,
        "title": "Add Customer",
        "cancel_url": reverse("customers:list"),
    })


@role_required(*CUSTOMER_EDIT_ROLES)
def customer_update(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    form = CustomerForm(request.POST or None, instance=customer)
    if request.method == "POST" and form.is_valid():
        changes = form_changes(form)
        form.save()
        if changes:
            log_audit(request, "updated", customer, changes=changes)
        messages.success(request, "Customer updated.")
        return redirect("customers:detail", pk=customer.pk)
    return render(request, "form_page.html", {
        "form": form,
        "title": f"Edit {customer.name}",
        "cancel_url": reverse("customers:detail", args=[customer.pk]),
    })



@role_required(*CUSTOMER_EDIT_ROLES)
@require_POST
def customer_toggle_archive(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    if customer.status == Customer.Status.ACTIVE:
        customer.status = Customer.Status.ARCHIVED
        action = "archived"
        messages.success(request, f"'{customer.name}' archived.")
    else:
        customer.status = Customer.Status.ACTIVE
        action = "restored"
        messages.success(request, f"'{customer.name}' is active again.")
    customer.save(update_fields=["status", "updated_at"])
    log_audit(request, action, customer)
    return redirect(request.POST.get("next") or "customers:list")


@role_required(*CUSTOMER_EDIT_ROLES)
@require_POST
def customer_portal_action(request, pk, action):
    customer = get_object_or_404(Customer, pk=pk)
    if action in ("enable", "regenerate"):
        if customer.status != Customer.Status.ACTIVE:
            messages.error(request, "Archived customers cannot have a portal link.")
            return redirect("customers:detail", pk=pk)
        if action == "regenerate" or not customer.portal_token:
            customer.new_portal_token()
        else:
            customer.portal_enabled = True
        label = "portal link regenerated" if action == "regenerate" else "portal enabled"
        messages.success(
            request,
            "A new link was created. The old link no longer works." if action == "regenerate"
            else "Client portal enabled.",
        )
    elif action == "disable":
        customer.disable_portal()
        label = "portal disabled"
        messages.success(request, "Client portal disabled. The link no longer works.")
    else:
        return redirect("customers:detail", pk=pk)
    customer.save(update_fields=["portal_enabled", "portal_token", "portal_token_created_at", "updated_at"])
    log_audit(request, label, customer, entity_type="Customer")
    return redirect("customers:detail", pk=pk)


@login_required
def customer_report_pdf(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    try:
        pdf = render_pdf(customer, client_orders(customer), portal_url_for(customer, request))
    except ImportError:
        messages.error(request, "PDF reports need reportlab: run 'pip install reportlab'.")
        return redirect("customers:detail", pk=pk)
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="report-{customer.pk}.pdf"'
    return response


@role_required(*CUSTOMER_EDIT_ROLES)
@require_POST
def customer_report_email(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    try:
        address = send_customer_report(customer, request=request, user=request.user)
    except DeliveryError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"Report emailed to {address}.")
    return redirect("customers:detail", pk=pk)


@role_required(*CUSTOMER_EDIT_ROLES)
@require_POST
def customer_whatsapp_send(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    try:
        whatsapp.send_whatsapp_report(customer, request=request, user=request.user)
    except whatsapp.WhatsAppError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "WhatsApp message sent.")
    return redirect("customers:detail", pk=pk)
