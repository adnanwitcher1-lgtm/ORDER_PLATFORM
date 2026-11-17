"""Customer-facing progress data and the branded PDF report.

Only data that is safe for a client is used here: no staff names, notes,
activity log or Google account data.
"""
import io
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from orders.forecast import estimate_eta
from orders.models import Order

S = Order.Status

# Internal status -> (client friendly label, tone used for the badge colour)
CLIENT_STATUS = {
    S.APPROVED: ("Starting soon", "blue"),
    S.ASSIGNED: ("Starting soon", "blue"),
    S.MONITORING: ("In progress", "cyan"),
    S.PAUSED: ("Paused", "gold"),
    S.TARGET_OBSERVED: ("Target reached - verifying", "orange"),
    S.COMPLETED: ("Completed", "green"),
    S.CANCELLED: ("Cancelled", "red"),
    S.EXCEPTION: ("Under review", "amber"),
}
# Drafts and orders waiting for internal approval are never shown to clients.
VISIBLE_STATUSES = tuple(CLIENT_STATUS)


def client_orders(customer, include_eta=None):
    if include_eta is None:
        include_eta = getattr(settings, "PORTAL_SHOW_ETA", True)
    orders = (
        Order.objects.filter(customer=customer, status__in=VISIBLE_STATUSES)
        .select_related("channel")
        .order_by("-created_at")
    )
    rows = []
    for order in orders:
        label, tone = CLIENT_STATUS[order.status]
        progress = order.progress
        eta = estimate_eta(order) if include_eta and order.status in (S.MONITORING, S.ASSIGNED) else None
        rows.append({
            "pk": order.pk,
            "number": order.order_number,
            "channel": order.channel.name,
            "status": order.status,
            "status_label": label,
            "tone": tone,
            "baseline": order.baseline_count,
            "target": order.target_count,
            "latest": order.latest_observed_count,
            "percent": progress.progress_percent if progress.has_observation else None,
            "reached": progress.target_reached,
            "updated_at": order.latest_observed_at,
            "approximate": order.latest_observed_accuracy == "approximate",
            "eta": eta.message if eta and eta.state == "ok" else "",
        })
    return rows


def summarize(rows):
    percents = [r["percent"] for r in rows if r["percent"] is not None and r["status"] in
                (S.MONITORING, S.ASSIGNED, S.PAUSED, S.TARGET_OBSERVED)]
    return {
        "total": len(rows),
        "in_progress": sum(1 for r in rows if r["status"] in (S.MONITORING, S.ASSIGNED, S.APPROVED)),
        "completed": sum(1 for r in rows if r["status"] == S.COMPLETED),
        "average": round(sum(percents) / len(percents)) if percents else 0,
    }


def has_reportable_orders(customer):
    return Order.objects.filter(customer=customer, status__in=VISIBLE_STATUSES).exists()


# --------------------------------------------------------------------------- PDF
def _fonts():
    """Use DejaVu if it is installed (better character coverage), else Helvetica."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    import os

    for base in ("/usr/share/fonts/truetype/dejavu/", "C:/Windows/Fonts/"):
        regular, bold = base + "DejaVuSans.ttf", base + "DejaVuSans-Bold.ttf"
        if os.path.exists(regular) and os.path.exists(bold):
            try:
                pdfmetrics.registerFont(TTFont("RptSans", regular))
                pdfmetrics.registerFont(TTFont("RptSans-Bold", bold))
                return "RptSans", "RptSans-Bold"
            except Exception:  # noqa: BLE001 - fall back to the built-in font
                pass
    return "Helvetica", "Helvetica-Bold"


def render_pdf(customer, rows=None, portal_url=""):
    """Return the PDF report as bytes. Needs reportlab."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import Flowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    rows = client_orders(customer) if rows is None else rows
    stats = summarize(rows)
    regular, bold = _fonts()
    brand = colors.HexColor(getattr(settings, "BRAND_COLOR", "#4f46e5"))
    ink, muted, line = colors.HexColor("#0f172a"), colors.HexColor("#64748b"), colors.HexColor("#e2e8f0")
    green = colors.HexColor("#16a34a")
    brand_name = getattr(settings, "BRAND_NAME", "Order Platform")

    small = ParagraphStyle("s", fontName=regular, fontSize=8.5, leading=11, textColor=muted)
    cell = ParagraphStyle("c", fontName=regular, fontSize=9, leading=12, textColor=ink)
    cell_b = ParagraphStyle("cb", parent=cell, fontName=bold)
    note = ParagraphStyle("n", fontName=regular, fontSize=8.5, leading=12, textColor=muted)

    class Bar(Flowable):
        def __init__(self, percent, width=3.4 * cm):
            super().__init__()
            self.percent, self.width, self.height = percent, width, 14

        def draw(self):
            c = self.canv
            c.setFillColor(line)
            c.roundRect(0, 5, self.width, 6, 3, stroke=0, fill=1)
            if self.percent:
                c.setFillColor(green if self.percent >= 100 else brand)
                c.roundRect(0, 5, max(self.width * self.percent / 100, 6), 6, 3, stroke=0, fill=1)

    width, _ = A4
    now = timezone.localtime()

    def page(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(brand)
        canvas.rect(0, A4[1] - 2.6 * cm, width, 2.6 * cm, stroke=0, fill=1)
        canvas.setFillColor(colors.white)
        canvas.setFont(bold, 17)
        canvas.drawString(2 * cm, A4[1] - 1.4 * cm, brand_name)
        canvas.setFont(regular, 10)
        canvas.drawString(2 * cm, A4[1] - 2.0 * cm, "Progress report")
        canvas.drawRightString(width - 2 * cm, A4[1] - 1.4 * cm, customer.name[:50])
        canvas.drawRightString(width - 2 * cm, A4[1] - 2.0 * cm, f"{now:%d %b %Y, %H:%M}")
        canvas.setFillColor(muted)
        canvas.setFont(regular, 8)
        canvas.drawString(2 * cm, 1.1 * cm, f"{brand_name} - generated automatically")
        canvas.drawRightString(width - 2 * cm, 1.1 * cm, f"Page {doc.page}")
        canvas.restoreState()

    story = [Spacer(1, 0.4 * cm)]
    tiles = [
        [str(stats["total"]), str(stats["in_progress"]), str(stats["completed"]), f'{stats["average"]}%'],
        ["Orders", "In progress", "Completed", "Average progress"],
    ]
    tile_table = Table(tiles, colWidths=[4.25 * cm] * 4)
    tile_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), bold), ("FONTSIZE", (0, 0), (-1, 0), 20),
        ("TEXTCOLOR", (0, 0), (-1, 0), brand),
        ("FONTNAME", (0, 1), (-1, 1), regular), ("FONTSIZE", (0, 1), (-1, 1), 9),
        ("TEXTCOLOR", (0, 1), (-1, 1), muted),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f3f5fb")),
        ("BOX", (0, 0), (-1, -1), 0.5, line),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.white),
        ("TOPPADDING", (0, 0), (-1, 0), 10), ("BOTTOMPADDING", (0, 1), (-1, 1), 10),
    ]))
    story += [tile_table, Spacer(1, 0.6 * cm)]

    header = [Paragraph(h, cell_b) for h in ("Order", "Channel", "Target", "Latest", "Progress", "Status")]
    data = [header]
    for r in rows:
        latest = "-" if r["latest"] is None else f'{r["latest"]:,}' + (" *" if r["approximate"] else "")
        pct = "No data" if r["percent"] is None else f'{r["percent"]}%'
        data.append([
            Paragraph(r["number"], cell_b),
            Paragraph(r["channel"], cell),
            Paragraph(f'{r["target"]:,}', cell),
            Paragraph(latest, cell),
            [Bar(r["percent"] or 0), Paragraph(pct, small)],
            Paragraph(r["status_label"], cell),
        ])
    if len(data) == 1:
        data.append([Paragraph("No orders to report yet.", cell), "", "", "", "", ""])
    table = Table(data, colWidths=[2.3 * cm, 4.0 * cm, 2.2 * cm, 2.4 * cm, 3.6 * cm, 2.5 * cm], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2ff")),
        ("LINEBELOW", (0, 0), (-1, -1), 0.5, line),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(table)
    story.append(Spacer(1, 0.5 * cm))
    story.append(Paragraph(
        "Counts are values observed on YouTube at the time shown. * marks an approximate count "
        "(YouTube rounds public numbers). This report records observed counts only; "
        "it is not a guarantee of future results.", note))
    if portal_url:
        story.append(Spacer(1, 0.3 * cm))
        story.append(Paragraph(f'Live progress: <a href="{portal_url}" color="#4f46e5">{portal_url}</a>', note))

    buffer = io.BytesIO()
    SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=3.4 * cm, bottomMargin=1.8 * cm,
        title=f"{brand_name} - progress report - {customer.name}",
    ).build(story, onFirstPage=page, onLaterPages=page)
    return buffer.getvalue()
