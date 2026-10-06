"""Shared typography, institutional branding and page furniture for metrology PDFs."""

from pathlib import Path

import matplotlib
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

NAVY = colors.HexColor("#0a2344")
MUTED = colors.HexColor("#516b91")
TEAL = colors.HexColor("#0099a9")
ORANGE = colors.HexColor("#ee7417")
RULE = colors.HexColor("#d6e3ef")
PALE = colors.HexColor("#eaf5f8")
PAGE_W, PAGE_H = A4
LEFT, RIGHT = 44, PAGE_W - 44
WIDTH = RIGHT - LEFT
DEFAULT_LAB = "Laboratório de Paleoceanografia e Paleoclimatologia"
ASSETS = Path(__file__).with_name("report_assets")


def register_fonts():
    if "Metrology" not in pdfmetrics.getRegisteredFontNames():
        fonts = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
        pdfmetrics.registerFont(TTFont("Metrology", str(fonts / "DejaVuSans.ttf")))
        pdfmetrics.registerFont(TTFont("Metrology-Bold", str(fonts / "DejaVuSans-Bold.ttf")))


def institutional(lab):
    return "p2l" in lab.lower() or "paleoceanograf" in lab.lower()


def draw_brand(c, lab):
    if institutional(lab):
        c.drawImage(str(ASSETS / "each-usp.png"), LEFT, PAGE_H - 83,
                    width=204, height=50.7, mask=[250, 255, 250, 255, 250, 255])
        c.drawImage(str(ASSETS / "p2l.png"), RIGHT - 174, PAGE_H - 84,
                    width=77, height=53.2, mask="auto")
        c.setFillColor(colors.HexColor("#174d64"))
        c.setFont("Metrology", 7)
        for line, y in zip(("LABORATÓRIO DE", "PALEOCEANOGRAFIA E", "PALEOCLIMATOLOGIA"),
                           (PAGE_H - 54, PAGE_H - 64, PAGE_H - 74)):
            c.drawString(RIGHT - 90, y, line)
    else:
        c.setFillColor(TEAL)
        c.circle(LEFT + 19, PAGE_H - 54, 19, stroke=0, fill=1)
        c.setFillColor(colors.white)
        c.setFont("Metrology-Bold", 11)
        c.drawCentredString(LEFT + 19, PAGE_H - 58, "IRMS")
        c.setFillColor(NAVY)
        c.setFont("Metrology-Bold", 12)
        c.drawString(LEFT + 51, PAGE_H - 58, "IRMS METROLOGY")


def draw_watermark(c, lab):
    c.saveState()
    if institutional(lab):
        c.setFillAlpha(.045)
        c.drawImage(str(ASSETS / "p2l.png"), 148, 152, width=310, height=214.3,
                    mask="auto")
    else:
        c.setFillColor(colors.HexColor("#f3f7fa"))
        c.circle(PAGE_W * .43, 270, 90, stroke=0, fill=1)
        c.circle(PAGE_W * .61, 219, 91, stroke=0, fill=1)
    c.restoreState()


def draw_rule(c, y=PAGE_H - 105):
    for start, fraction, shade in ((0, .53, TEAL), (.53, .25, colors.HexColor("#07547a")),
                                    (.78, .22, ORANGE)):
        c.setFillColor(shade)
        c.rect(LEFT + WIDTH * start, y, WIDTH * fraction, 2, stroke=0, fill=1)


def draw_report_page(c, document, *, lab, title, subtitle, number, issue_date, simulation):
    c.saveState()
    c.setFillColor(colors.HexColor("#f6fafc"))
    c.rect(0, PAGE_H - 173, PAGE_W, 173, stroke=0, fill=1)
    draw_watermark(c, lab)
    draw_brand(c, lab)
    draw_rule(c)
    c.setFillColor(NAVY)
    c.setFont("Metrology-Bold", 24)
    c.drawString(LEFT, PAGE_H - 144, title)
    c.setFont("Metrology", 8)
    c.setFillColor(MUTED)
    c.drawString(LEFT, PAGE_H - 160, subtitle)
    c.setFont("Metrology-Bold", 7)
    c.drawString(RIGHT - 166, PAGE_H - 125, "Report number")
    c.drawString(RIGHT - 166, PAGE_H - 153, "Issue date")
    c.setFont("Metrology", 8)
    c.setFillColor(NAVY)
    c.drawString(RIGHT - 166, PAGE_H - 137, number)
    c.drawString(RIGHT - 166, PAGE_H - 165, issue_date)
    c.setStrokeColor(RULE)
    c.setLineWidth(.7)
    c.line(LEFT, 44, RIGHT, 44)
    c.setFillColor(MUTED)
    c.setFont("Metrology", 6.5)
    c.drawString(LEFT, 30, number)
    c.drawRightString(RIGHT, 30, f"Page {document.page}")
    if simulation:
        c.setFillColor(ORANGE)
        c.setFont("Metrology-Bold", 7)
        c.drawCentredString(PAGE_W / 2, 17,
                           "DEMONSTRATION ONLY - NOT A LABORATORY CERTIFICATE")
    c.restoreState()
