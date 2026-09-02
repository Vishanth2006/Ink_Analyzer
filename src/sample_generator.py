from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib import colors


def create_sample_pdf(file_path):
    """Generates a 2-page sample PDF with CMYK elements to test the analyzer."""
    c = canvas.Canvas(file_path, pagesize=letter)

    c.setFont("Helvetica-Bold", 24)
    c.drawString(80, 720, "CMYK Ink Analyzer Test Page 1")

    c.setFont("Helvetica", 10.5)
    c.setFillColor(colors.HexColor("#4B5563"))
    c.drawString(80, 690, "This page simulates solid printer inks to test CMYK channel split logic.")

    c.setFillColor(colors.CMYKColor(1, 0, 0, 0))
    c.rect(80, 520, 90, 120, fill=True, stroke=False)
    c.setFillColor(colors.HexColor("#111827"))
    c.setFont("Helvetica-Bold", 10)
    c.drawString(80, 500, "Cyan (100%)")

    c.setFillColor(colors.CMYKColor(0, 1, 0, 0))
    c.rect(190, 520, 90, 120, fill=True, stroke=False)
    c.setFillColor(colors.HexColor("#111827"))
    c.drawString(190, 500, "Magenta (100%)")

    c.setFillColor(colors.CMYKColor(0, 0, 1, 0))
    c.rect(300, 520, 90, 120, fill=True, stroke=False)
    c.setFillColor(colors.HexColor("#111827"))
    c.drawString(300, 500, "Yellow (100%)")

    c.setFillColor(colors.CMYKColor(0, 0, 0, 1))
    c.rect(410, 520, 90, 120, fill=True, stroke=False)
    c.setFillColor(colors.HexColor("#111827"))
    c.drawString(410, 500, "Black (100%)")

    c.setFillColor(colors.CMYKColor(0.85, 0.85, 0.80, 0.80))
    c.rect(80, 260, 420, 180, fill=True, stroke=False)

    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(100, 360, "High Ink Density Zone")
    c.setFont("Helvetica", 10.5)
    c.drawString(100, 340, "This area demonstrates high combined CMYK coverage.")

    c.showPage()

    c.setFillColor(colors.HexColor("#111827"))
    c.setFont("Helvetica-Bold", 24)
    c.drawString(80, 720, "CMYK Ink Analyzer Test Page 2")

    c.setFont("Helvetica", 10.5)
    c.setFillColor(colors.HexColor("#4B5563"))
    c.drawString(80, 690, "This page simulates a standard monochrome text document layout.")

    c.setFillColor(colors.CMYKColor(0, 0, 0, 0.9))
    c.setFont("Helvetica", 11)
    y = 630
    for i in range(20):
        c.drawString(80, y, f"Lorem ipsum dolor sit amet, consectetur adipiscing elit. Line {i+1} of simulated page body text.")
        y -= 22

    c.showPage()
    c.save()
