import io
import csv
from datetime import datetime

from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.pdfgen import canvas


class NumberedCanvas(canvas.Canvas):
    """Two-pass canvas to dynamically calculate and draw total page numbers and page headers/footers."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, page_count):
        self.saveState()
        self.setFont("Helvetica", 8)
        self.setFillColor(colors.HexColor("#4B5563"))

        if self._pageNumber > 1:
            self.drawString(54, 750, "PDF CMYK Ink Coverage Report")
            self.setStrokeColor(colors.HexColor("#E5E7EB"))
            self.setLineWidth(0.5)
            self.line(54, 742, 558, 742)

        page_text = f"Page {self._pageNumber} of {page_count}"
        self.drawRightString(558, 40, page_text)
        self.drawString(54, 40, f"Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        self.setStrokeColor(colors.HexColor("#E5E7EB"))
        self.setLineWidth(0.5)
        self.line(54, 52, 558, 52)

        self.restoreState()


def _resolve_ink_rates(rate_input):
    if isinstance(rate_input, dict):
        return {
            "Cyan": float(rate_input.get("Cyan", 0.0)),
            "Magenta": float(rate_input.get("Magenta", 0.0)),
            "Yellow": float(rate_input.get("Yellow", 0.0)),
            "Black": float(rate_input.get("Black", 0.0)),
        }
    scalar = float(rate_input)
    return {
        "Cyan": scalar,
        "Magenta": scalar,
        "Yellow": scalar,
        "Black": scalar,
    }


def generate_csv_report(page_results, consumption_rate, metadata=None):
    """Generates a CSV report with CMYK coverage and ink usage without print-order factors."""
    output = io.StringIO()
    writer = csv.writer(output)
    rates = _resolve_ink_rates(consumption_rate)

    if metadata:
        writer.writerow(["Document Metadata"])
        writer.writerow(["Filename", metadata.get("original_filename") or metadata.get("filename") or "-"])
        writer.writerow(["Date", metadata.get("file_date_display") or metadata.get("file_date") or "-"])
        writer.writerow(["Edition", metadata.get("edition_name") or metadata.get("edition_code") or "-"])
        writer.writerow(["Page Number", metadata.get("page_number") or "-"])
        writer.writerow([])

    writer.writerow([
        "Page",
        "Width (in)",
        "Height (in)",
        "Area (m2)",
        "Cyan Coverage (%)",
        "Magenta Coverage (%)",
        "Yellow Coverage (%)",
        "Black Coverage (%)",
        "Ink Volume / Page (kg)"
    ])

    total_ink = 0.0
    for p in page_results:
        page_ink = (
            (p["area_m2"] * (p["cyan"] / 100.0) * (rates["Cyan"] / 1000.0))
            + (p["area_m2"] * (p["magenta"] / 100.0) * (rates["Magenta"] / 1000.0))
            + (p["area_m2"] * (p["yellow"] / 100.0) * (rates["Yellow"] / 1000.0))
            + (p["area_m2"] * (p["black"] / 100.0) * (rates["Black"] / 1000.0))
        )
        total_ink += page_ink
        writer.writerow([
            p["page_num"],
            f"{p['width_in']:.2f}",
            f"{p['height_in']:.2f}",
            f"{p['area_m2']:.4f}",
            f"{p['cyan']:.2f}",
            f"{p['magenta']:.2f}",
            f"{p['yellow']:.2f}",
            f"{p['black']:.2f}",
            f"{page_ink:.7f}"
        ])

    writer.writerow([])
    writer.writerow(["DOCUMENT TOTALS / AVERAGES"])
    avg_cyan = sum(p["cyan"] for p in page_results) / len(page_results)
    avg_magenta = sum(p["magenta"] for p in page_results) / len(page_results)
    avg_yellow = sum(p["yellow"] for p in page_results) / len(page_results)
    avg_black = sum(p["black"] for p in page_results) / len(page_results)

    writer.writerow([
        "Total/Avg",
        "-",
        "-",
        f"{sum(p['area_m2'] for p in page_results):.4f}",
        f"{avg_cyan:.2f} (Avg)",
        f"{avg_magenta:.2f} (Avg)",
        f"{avg_yellow:.2f} (Avg)",
        f"{avg_black:.2f} (Avg)",
        f"{total_ink:.7f}"
    ])

    return output.getvalue()


def generate_pdf_report(pdf_filename, page_results, consumption_rate, engine_name, metadata=None):
    """Generates a PDF summary report without any print-order data."""
    rates = _resolve_ink_rates(consumption_rate)
    buffer = io.BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=54,
        rightMargin=54,
        topMargin=54,
        bottomMargin=54,
    )

    styles = getSampleStyleSheet()
    primary_color = colors.HexColor("#1E3A8A")
    secondary_color = colors.HexColor("#3B82F6")
    neutral_dark = colors.HexColor("#1F2937")
    neutral_light = colors.HexColor("#F9FAFB")
    border_color = colors.HexColor("#E5E7EB")

    styles['Normal'].textColor = neutral_dark
    styles['Normal'].fontSize = 10
    styles['Normal'].leading = 14

    title_style = ParagraphStyle('DocTitle', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=24, leading=28, textColor=primary_color, spaceAfter=6)
    subtitle_style = ParagraphStyle('DocSubtitle', parent=styles['Normal'], fontName='Helvetica', fontSize=11, leading=15, textColor=colors.HexColor('#4B5563'), spaceAfter=15)
    section_heading = ParagraphStyle('SecHeading', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=14, leading=18, textColor=primary_color, spaceBefore=12, spaceAfter=6, keepWithNext=True)
    kpi_title_style = ParagraphStyle('KPITitle', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=9, leading=12, textColor=colors.HexColor('#4B5563'), alignment=1)
    kpi_value_style = ParagraphStyle('KPIValue', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=15, leading=19, textColor=primary_color, alignment=1)
    tbl_header_style = ParagraphStyle('TblHeader', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=8.5, leading=11, textColor=colors.white, alignment=1)
    tbl_cell_style = ParagraphStyle('TblCell', parent=styles['Normal'], fontName='Helvetica', fontSize=8.5, leading=11, alignment=1)
    tbl_cell_bold = ParagraphStyle('TblCellBold', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=8.5, leading=11, alignment=1)

    story = []
    source_label = metadata.get("original_filename") if metadata else pdf_filename
    file_date_display = metadata.get("file_date_display") if metadata else None
    edition_name = metadata.get("edition_name") if metadata else None
    page_number = metadata.get("page_number") if metadata else None

    story.append(Paragraph("PDF CMYK Ink Coverage Report", title_style))
    story.append(Paragraph(
        f"Source Document: {source_label} | Date: {file_date_display or 'Not Parsed'} | Edition: {edition_name or 'Unknown'} | Page: {page_number if page_number is not None else 'N/A'} | Run Date: {datetime.now().strftime('%B %d, %Y')}",
        subtitle_style,
    ))
    story.append(Spacer(1, 10))

    total_pages = len(page_results)
    avg_c = sum(p["cyan"] for p in page_results) / total_pages
    avg_m = sum(p["magenta"] for p in page_results) / total_pages
    avg_y = sum(p["yellow"] for p in page_results) / total_pages
    avg_k = sum(p["black"] for p in page_results) / total_pages
    channel_avgs = {"Cyan": avg_c, "Magenta": avg_m, "Yellow": avg_y, "Black": avg_k}
    dom_ink = max(channel_avgs, key=channel_avgs.get)

    total_ink = 0.0
    for p in page_results:
        total_ink += (
            (p["area_m2"] * (p["cyan"] / 100.0) * (rates["Cyan"] / 1000.0))
            + (p["area_m2"] * (p["magenta"] / 100.0) * (rates["Magenta"] / 1000.0))
            + (p["area_m2"] * (p["yellow"] / 100.0) * (rates["Yellow"] / 1000.0))
            + (p["area_m2"] * (p["black"] / 100.0) * (rates["Black"] / 1000.0))
        )

    kpi_data = [
        [Paragraph("Total Pages", kpi_title_style), Paragraph("Total Ink Required", kpi_title_style), Paragraph("Dominant Ink", kpi_title_style)],
        [Paragraph(str(total_pages), kpi_value_style), Paragraph(f"{total_ink:.6f} kg", kpi_value_style), Paragraph(f"{dom_ink} ({channel_avgs[dom_ink]:.1f}%)", kpi_value_style)]
    ]
    kpi_table = Table(kpi_data, colWidths=[170, 170, 170])
    kpi_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), neutral_light),
        ('BOX', (0, 0), (-1, -1), 1, border_color),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, border_color),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    story.append(kpi_table)
    story.append(Spacer(1, 15))

    story.append(Paragraph("Analysis Parameters", section_heading))
    overall_rate = sum(rates.values())
    config_data = [
        [Paragraph("<b>Rendering Engine:</b>", tbl_cell_style), Paragraph(engine_name, tbl_cell_style), Paragraph("<b>Ink Consumption Rate:</b>", tbl_cell_style), Paragraph(f"{overall_rate:.2f} g/m² (100% cover)", tbl_cell_style)],
        [Paragraph("<b>Total Ink Consumed:</b>", tbl_cell_style), Paragraph(f"{total_ink:.6f} kg", tbl_cell_style), Paragraph("", tbl_cell_style), Paragraph("", tbl_cell_style)]
    ]
    config_table = Table(config_data, colWidths=[130, 122, 130, 122])
    config_table.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 0.5, border_color),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, border_color),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('BACKGROUND', (0, 0), (0, -1), neutral_light),
        ('BACKGROUND', (2, 0), (2, -1), neutral_light),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    story.append(config_table)
    story.append(Spacer(1, 15))

    story.append(Spacer(1, 10))

    page_story = []
    page_story.append(Paragraph("Detailed Page-by-Page Statistics", section_heading))
    page_headers = [Paragraph("Page", tbl_header_style), Paragraph("Cyan %", tbl_header_style), Paragraph("Mag %", tbl_header_style), Paragraph("Yel %", tbl_header_style), Paragraph("Blk %", tbl_header_style), Paragraph("Ink (kg)", tbl_header_style)]
    page_table_data = [page_headers]

    for p in page_results:
        page_ink = (
            (p["area_m2"] * (p["cyan"] / 100.0) * (rates["Cyan"] / 1000.0))
            + (p["area_m2"] * (p["magenta"] / 100.0) * (rates["Magenta"] / 1000.0))
            + (p["area_m2"] * (p["yellow"] / 100.0) * (rates["Yellow"] / 1000.0))
            + (p["area_m2"] * (p["black"] / 100.0) * (rates["Black"] / 1000.0))
        )
        row = [
            Paragraph(f"<b>{p['page_num']}</b>", tbl_cell_style),
            Paragraph(f"{p['cyan']:.1f}%", tbl_cell_style),
            Paragraph(f"{p['magenta']:.1f}%", tbl_cell_style),
            Paragraph(f"{p['yellow']:.1f}%", tbl_cell_style),
            Paragraph(f"{p['black']:.1f}%", tbl_cell_style),
            Paragraph(f"{page_ink:.6f}", tbl_cell_bold),
        ]
        page_table_data.append(row)

    page_table = Table(page_table_data, colWidths=[42, 60, 60, 60, 60, 90])
    page_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), secondary_color),
        ('BOX', (0, 0), (-1, -1), 0.5, border_color),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, border_color),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, neutral_light]),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    page_story.append(page_table)
    story.append(KeepTogether(page_story))

    doc.build(story, canvasmaker=NumberedCanvas)
    buffer.seek(0)
    return buffer.getvalue()

