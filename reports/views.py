from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils import timezone
from audit.services import log_audit
from .exports import PdfUnavailable, csv_response, pdf_response
from .forms import ReportFilterForm
from .registry import REPORT_MAP, can_view, visible_filters, visible_reports

DISPLAY_LIMIT = 500


def _resolve(request, key):
    definition = REPORT_MAP.get(key)
    if definition is None:
        raise Http404
    if not can_view(request.user, definition):
        raise PermissionDenied

    params = request.GET.copy()
    if definition.default_days and "date_from" not in params and "date_to" not in params:
        today = timezone.localdate()
        params["date_from"] = (today - timedelta(days=definition.default_days - 1)).isoformat()
        params["date_to"] = today.isoformat()

    choices = definition.status_choices() if definition.status_choices else ()
    form = ReportFilterForm(
        params, filters=visible_filters(request.user, definition), status_choices=choices
    )
    valid = form.is_valid()
    applied = {k: v for k, v in form.cleaned_data.items() if v not in (None, "")} if valid else {}
    return definition, form, valid, params, applied


def _describe(applied):
    labels = {"date_from": "From", "date_to": "To", "customer": "Customer", "status": "Status", "staff": "Staff"}
    return ", ".join(f"{label}: {applied[key]}" for key, label in labels.items() if key in applied)


@login_required
def report_index(request):
    return render(request, "reports/index.html", {"reports": visible_reports(request.user)})


@login_required
def report_detail(request, key):
    definition, form, valid, params, applied = _resolve(request, key)
    data = definition.build(request.user, applied) if valid else None
    return render(request, "reports/report.html", {
        "report": definition,
        "form": form,
        "valid": valid,
        "data": data,
        "rows": data.rows[:DISPLAY_LIMIT] if data else [],
        "display_truncated": bool(data and len(data.rows) > DISPLAY_LIMIT),
        "display_limit": DISPLAY_LIMIT,
        "querystring": params.urlencode(),
    })


@login_required
def report_export(request, key, fmt):
    if fmt not in ("csv", "pdf"):
        raise Http404
    definition, form, valid, params, applied = _resolve(request, key)
    if not valid:
        messages.error(request, "The filters are invalid, so the export failed.")
        return redirect("reports:detail", key=key)

    data = definition.build(request.user, applied)
    data = definition.build(request.user, applied)
    filename = f"{key}_{timezone.localdate():%Y%m%d}.{fmt}"

    if fmt == "csv":
        response = csv_response(data, filename)
    else:
        try:
            response = pdf_response(data, filename, request.user, _describe(applied))
        except PdfUnavailable:
            messages.error(request, "PDF export needs reportlab: run 'pip install reportlab' in the terminal.")
            return redirect("reports:detail", key=key)

    log_audit(
        request, "exported", entity_type="Report",
        entity_label=f"{definition.title} ({fmt.upper()})",
        changes={"filters": _describe(applied) or "none", "rows": len(data.rows)},
    )
    return response