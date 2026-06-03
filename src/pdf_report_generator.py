"""
PDF Report Generator – Tạo file PDF để tải từ dashboard.
"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Iterable
from xml.sax.saxutils import escape


STATUS_COLOR = {
    "OK": "#28a745",
    "WARNING": "#ffc107",
    "CRITICAL": "#dc3545",
    "ERROR": "#6c757d",
}

STATUS_LABEL = {
    "OK": "OK",
    "WARNING": "CẢNH BÁO",
    "CRITICAL": "LỖI NGHIÊM TRỌNG",
    "ERROR": "LỖI",
}


def _register_font():
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    candidates = [
        (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ),
        (
            "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
            "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
        ),
        (
            "/usr/share/fonts/truetype/msttcorefonts/Arial.ttf",
            "/usr/share/fonts/truetype/msttcorefonts/Arial_Bold.ttf",
        ),
        (
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        ),
        (
            "/Library/Fonts/Arial Unicode.ttf",
            "/Library/Fonts/Arial Bold.ttf",
        ),
        (
            "C:/Windows/Fonts/arial.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
        ),
    ]
    for regular_path, bold_path in candidates:
        regular = Path(regular_path)
        bold = Path(bold_path)
        if regular.exists():
            pdfmetrics.registerFont(TTFont("SIMOFont", str(regular)))
            if bold.exists():
                pdfmetrics.registerFont(TTFont("SIMOFont-Bold", str(bold)))
            else:
                pdfmetrics.registerFont(TTFont("SIMOFont-Bold", str(regular)))
            return "SIMOFont", "SIMOFont-Bold"
    return "Helvetica", "Helvetica-Bold"


def _short(value: object, max_len: int = 180) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _pdf_text(value: object, max_len: int = 180) -> str:
    return escape(_short(value, max_len))


def _format_dt(value: object) -> str:
    text = str(value or "")
    return text[:19].replace("T", " ") if text else "-"


def _group_results(results: Iterable[dict]) -> dict[str, list[dict]]:
    systems: dict[str, list[dict]] = {}
    for result in results:
        systems.setdefault(result.get("system") or "-", []).append(result)
    return systems


def generate_pdf_report(run_info: dict, results: list[dict]) -> BytesIO:
    """Tạo PDF report và trả về BytesIO ở vị trí đầu stream."""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        SimpleDocTemplate,
        Paragraph,
        Spacer,
        Table,
        TableStyle,
        PageBreak,
    )

    font_name, bold_font_name = _register_font()
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
        title=f"SIMO Report {run_info.get('run_id', '')}",
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "SIMOTitle",
        parent=styles["Title"],
        fontName=bold_font_name,
        fontSize=18,
        leading=22,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#212529"),
        spaceAfter=8,
    )
    subtitle_style = ParagraphStyle(
        "SIMOSubtitle",
        parent=styles["Normal"],
        fontName=font_name,
        fontSize=9,
        leading=12,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#495057"),
        spaceAfter=10,
    )
    section_style = ParagraphStyle(
        "SIMOSection",
        parent=styles["Heading2"],
        fontName=bold_font_name,
        fontSize=12,
        leading=15,
        textColor=colors.HexColor("#212529"),
        spaceBefore=8,
        spaceAfter=6,
    )
    normal_style = ParagraphStyle(
        "SIMONormal",
        parent=styles["Normal"],
        fontName=font_name,
        fontSize=8,
        leading=10,
    )
    small_style = ParagraphStyle(
        "SIMOSmall",
        parent=normal_style,
        fontSize=7,
        leading=9,
        splitLongWords=1,
    )

    report_title = "SIMO Daily Check Report"
    if run_info.get("run_type") == "dynamic_module" and run_info.get("module_name"):
        report_title = f"SIMO Dynamic Module Report - {run_info['module_name']}"

    story = [
        Paragraph(_pdf_text(report_title, 160), title_style),
        Paragraph(
            "Run ID: {run_id} | Loại run: {run_type} | Module: {module} | Trạng thái: {status}".format(
                run_id=_pdf_text(run_info.get("run_id", "-")),
                run_type=_pdf_text(run_info.get("run_type", "-")),
                module=_pdf_text(run_info.get("module_name") or "-"),
                status=STATUS_LABEL.get(run_info.get("overall_status"), run_info.get("overall_status", "-")),
            ),
            subtitle_style,
        ),
    ]

    summary_rows = [
        ["Bắt đầu", "Kết thúc", "Tổng", "OK", "Warning", "Critical", "Error"],
        [
            _format_dt(run_info.get("started_at")),
            _format_dt(run_info.get("finished_at")),
            str(run_info.get("total_checks", 0)),
            str(run_info.get("ok_count", 0)),
            str(run_info.get("warning_count", 0)),
            str(run_info.get("critical_count", 0)),
            str(run_info.get("error_count", 0)),
        ],
    ]
    summary_table = Table(summary_rows, repeatRows=1, hAlign="CENTER")
    summary_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), font_name),
        ("FONTNAME", (0, 0), (-1, 0), bold_font_name),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#343a40")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#dee2e6")),
        ("BACKGROUND", (0, 1), (-1, 1), colors.HexColor("#f8f9fa")),
    ]))
    story.extend([summary_table, Spacer(1, 8)])

    issues = [result for result in results if result.get("status") != "OK"]
    story.append(Paragraph("Tóm tắt bất thường", section_style))
    if issues:
        story.append(_results_table(issues, font_name, bold_font_name, small_style, include_system=True))
    else:
        story.append(Paragraph("Không có cảnh báo hoặc lỗi trong lần chạy này.", normal_style))

    story.append(PageBreak())
    story.append(Paragraph("Chi tiết đầy đủ", section_style))
    for system, system_results in _group_results(results).items():
        story.append(Paragraph(_pdf_text(str(system).upper(), 120), section_style))
        story.append(_results_table(system_results, font_name, bold_font_name, small_style, include_system=False))
        story.append(Spacer(1, 6))

    doc.build(story, onFirstPage=_draw_footer(font_name), onLaterPages=_draw_footer(font_name))
    buffer.seek(0)
    return buffer


def _results_table(
    results: list[dict],
    font_name: str,
    bold_font_name: str,
    small_style,
    include_system: bool,
):
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, Table, TableStyle

    headers = ["Host", "Kiểm tra", "Trạng thái", "Chi tiết", "Thời gian"]
    widths = [32 * mm, 60 * mm, 27 * mm, 94 * mm, 22 * mm]
    if include_system:
        headers.insert(0, "Hệ thống")
        widths = [25 * mm, 30 * mm, 56 * mm, 26 * mm, 88 * mm, 22 * mm]

    rows = [headers]
    for result in results:
        row = [
            Paragraph(_pdf_text(result.get("host"), 80), small_style),
            Paragraph(_pdf_text(result.get("check_name"), 120), small_style),
            Paragraph(_pdf_text(STATUS_LABEL.get(result.get("status"), result.get("status", "-")), 40), small_style),
            Paragraph(_pdf_text(result.get("message"), 260), small_style),
            Paragraph(_pdf_text(_format_dt(result.get("checked_at"))[-8:], 20), small_style),
        ]
        if include_system:
            row.insert(0, Paragraph(_pdf_text(result.get("system"), 60), small_style))
        rows.append(row)

    table = Table(rows, repeatRows=1, colWidths=widths)
    style = TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), font_name),
        ("FONTNAME", (0, 0), (-1, 0), bold_font_name),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#343a40")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#dee2e6")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8f9fa")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ])
    for row_index, result in enumerate(results, start=1):
        color = STATUS_COLOR.get(result.get("status"))
        if color:
            status_col = 3 if include_system else 2
            style.add("TEXTCOLOR", (status_col, row_index), (status_col, row_index), colors.HexColor(color))
    table.setStyle(style)
    return table


def _draw_footer(font_name: str):
    def draw(canvas, doc):
        canvas.saveState()
        canvas.setFont(font_name, 7)
        canvas.setFillColorRGB(0.45, 0.45, 0.45)
        canvas.drawCentredString(
            doc.pagesize[0] / 2,
            8 * mm,
            f"SIMO Daily Check System | Page {doc.page}",
        )
        canvas.restoreState()

    from reportlab.lib.units import mm

    return draw
