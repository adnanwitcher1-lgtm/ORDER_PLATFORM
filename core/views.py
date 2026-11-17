from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count
from django.db.models.functions import TruncDate
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django.urls import reverse
from django.utils import timezone
from django.views.generic import TemplateView

from accounts.permissions import GOOGLE_VIEW_ROLES, role_required
from audit.services import form_changes, log_audit
from customers.models import Customer
from googleaccounts.models import GoogleAccountConnection
from orders.models import Order

from .forms import SiteSettingsForm
from .models import SiteSettings


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "core/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        today = timezone.localdate()
        orders_url = reverse("orders:list")

        awaiting_review = Order.objects.filter(status=Order.Status.TARGET_OBSERVED).count()

        context["stats"] = [
            {
                "label": "Total Customers",
                "value": Customer.objects.filter(status=Customer.Status.ACTIVE).count(),
                "color": "blue",
                "icon": "\U0001F465",
                "hint": "Active customers",
                "url": reverse("customers:list"),
            },
            {
                "label": "Active Orders",
                "value": Order.objects.filter(status__in=Order.ACTIVE_STATUSES).count(),
                "color": "green",
                "icon": "\U0001F4E6",
                "hint": "Approved, assigned or monitoring",
                "url": orders_url,
            },
            {
                "label": "Created Today",
                "value": Order.objects.filter(created_at__date=today).count(),
                "color": "amber",
                "icon": "\u2728",
                "hint": "New orders today",
                "url": orders_url,
            },
            {
                "label": "Awaiting Review",
                "value": awaiting_review,
                "color": "red",
                "icon": "\U0001F514",
                "hint": "Target observed, needs review",
                "url": f"{orders_url}?status={Order.Status.TARGET_OBSERVED}",
            },
        ]
        context["awaiting_review"] = awaiting_review
        context["recent_orders"] = Order.objects.select_related("customer", "channel")[:5]

        context["chart_data"] = self.build_chart_data(today)
        from orders.anomalies import find_stalled
        context["attention"] = [
            {"order": o, "count": count} for o, count in find_stalled()[:5]
        ]

        context["show_google"] = user.is_superuser or user.role in GOOGLE_VIEW_ROLES
        context["reauth_count"] = GoogleAccountConnection.objects.filter(
            status=GoogleAccountConnection.Status.NEEDS_REAUTH
        ).count()
        return context

    @staticmethod
    def build_chart_data(today):
        """Plain numbers for the dashboard charts (rendered with Chart.js)."""
        # 1. Orders by status
        counts = dict(
            Order.objects.values_list("status").annotate(n=Count("id")).order_by()
        )
        status = [
            {"key": key, "label": label, "count": counts.get(key, 0)}
            for key, label in Order.Status.choices
        ]

        # 2. Orders created per day (last 14 days, zero-filled)
        days = [today - timedelta(days=i) for i in range(13, -1, -1)]
        per_day = {
            row["day"]: row["n"]
            for row in Order.objects.filter(created_at__date__gte=days[0])
            .annotate(day=TruncDate("created_at"))
            .values("day")
            .annotate(n=Count("id"))
            .order_by()
        }
        daily = {
            "labels": [d.strftime("%d %b") for d in days],
            "values": [per_day.get(d, 0) for d in days],
        }

        # 3. Top customers by number of orders
        top = (
            Order.objects.values("customer__name")
            .annotate(n=Count("id"))
            .order_by("-n", "customer__name")[:5]
        )
        customers = {
            "labels": [row["customer__name"] for row in top],
            "values": [row["n"] for row in top],
        }

        # 4. Average progress of orders that have at least one observation
        tracked = Order.objects.filter(
            status__in=Order.ACTIVE_STATUSES + (Order.Status.TARGET_OBSERVED,),
            latest_observed_count__isnull=False,
        )[:500]
        percents = [o.progress.progress_percent for o in tracked]
        average = round(sum(percents) / len(percents)) if percents else 0

        return {
            "status": status,
            "daily": daily,
            "customers": customers,
            "progress": {"average": average, "tracked": len(percents)},
            "total_orders": sum(counts.values()),
        }


@login_required
@require_POST
def ai_summary(request, scope, pk=None):
    """JSON: a short plain-language summary of an order, a customer, or everything."""
    from customers.models import Customer
    from orders.insights import customer_facts, order_facts, portfolio_facts, summarize

    lang = "ur" if request.POST.get("lang") == "ur" else "en"
    if scope == "order":
        facts = order_facts(get_object_or_404(Order.objects.select_related("customer", "channel"), pk=pk))
        kind = "order"
    elif scope == "customer":
        facts = customer_facts(get_object_or_404(Customer, pk=pk))
        kind = "group"
    elif scope == "portfolio":
        facts, kind = portfolio_facts(), "group"
    else:
        return JsonResponse({"error": "Unknown scope."}, status=404)
    text, source, note = summarize(kind, facts, lang, request.user)
    return JsonResponse({"summary": text, "source": source, "note": note})


@role_required()   # no role: Super Admin only
def site_settings(request):
    current = SiteSettings.load()
    form = SiteSettingsForm(request.POST or None, instance=current)
    if request.method == "POST" and form.is_valid():
        changes = form_changes(form)
        updated = form.save(commit=False)
        updated.updated_by = request.user
        updated.save()
        if changes:
            log_audit(request, "updated", updated, entity_type="Settings",
                      entity_label="System settings", changes=changes)
        messages.success(request, "Settings saved. The new intervals apply from the next check.")
        return redirect("core:settings")
    return render(request, "form_page.html", {
        "form": form,
        "title": "System Settings",
        "cancel_url": reverse("core:dashboard"),
    })