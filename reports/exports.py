import csv
from io import BytesIO
from xml.sax.saxutils import escape

from django.http import HttpResponse
from django.utils import timezone

FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
PDF_ROW_LIMIT = 1000


class PdfUnavailable(Exception):
    """reportlab is not installed."""


def safe_cell(value):
    """Text that could become a formula (=, +, -, @) gets a leading '. Numbers stay as they are."""
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return value
    text = str(value)
    return "'" + text if text.startswith(FORMULA_PREFIXES) else text


def csv_response(data, filename):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.write("\ufeff")   # so Excel shows UTF-8 text correctly
    writer = csv.writer(response)
    writer.writerow(data.columns)
    for row in data.rows:
        writer.writerow([safe_cell(value) for value in row])
    return response


def pdf_response(data, filename, generated_by, filter_text=""):
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ImportError:
        raise PdfUnavailable

    page = landscape(A4)
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=page, title=data.title,
        leftMargin=12 * mm, rightMargin=12 * mm, topMargin=12 * mm, bottomMargin=14 * mm,
    )
    styles = getSampleStyleSheet()
    cell = ParagraphStyle("cell", parent=styles["BodyText"], fontSize=7, leading=9)
    head = ParagraphStyle("head", parent=cell, fontName="Helvetica-Bold", textColor=colors.white)
    small = ParagraphStyle("small", parent=styles["Normal"], fontSize=8, textColor=colors.HexColor("#475569"))

    meta = f"Generated {timezone.localtime():%d %b %Y %H:%M} by {generated_by.display_name}"
    if filter_text:
        meta += f" | {filter_text}"
    story = [Paragraph(escape(data.title), styles["Title"]), Paragraph(escape(meta), small)]
    for label, value in data.summary:
        story.append(Paragraph(f"<b>{escape(str(label))}:</b> {escape(str(value))}", small))
    story.append(Spacer(1, 6))

    rows = data.rows[:PDF_ROW_LIMIT]
    table_data = [[Paragraph(escape(str(c)), head) for c in data.columns]]
    for row in rows:
        table_data.append([Paragraph(escape("" if v is None else str(v)), cell) for v in row])

    table = Table(table_data, colWidths=[doc.width / len(data.columns)] * len(data.columns), repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f172a")),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cbd5e1")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
    ]))
    story.append(table)

    notes = [data.note] if data.note else []
    if len(data.rows) > PDF_ROW_LIMIT:
        notes.append(f"The PDF contains only the first {PDF_ROW_LIMIT} rows; the full data is in the CSV.")
    for note in notes:
        story += [Spacer(1, 6), Paragraph(escape(note), small)]

    def footer(canvas, document):
        canvas.setFont("Helvetica", 7)
        canvas.drawRightString(page[0] - 12 * mm, 8 * mm, f"Page {document.page}")

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response