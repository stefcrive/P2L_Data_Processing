"""Presentation of an immutable, released client result as a certificate."""

from __future__ import annotations

import io
from datetime import datetime
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph


from .report_theme import (
    DEFAULT_LAB, LEFT, MUTED, NAVY, ORANGE, PALE, PAGE_H, PAGE_W, RIGHT, RULE,
    WIDTH, draw_brand, draw_rule, draw_watermark,
)


def _date(value: str) -> str:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return f"{dt.day} {dt.strftime('%B %Y')}"
    except (ValueError, TypeError):
        return str(value)


def _paragraph(c, value, x, top, width, *, size=9, leading=13, bold=False, color=NAVY,
               align=0):
    style = ParagraphStyle(
        "certificate", fontName="Metrology-Bold" if bold else "Metrology",
        fontSize=size, leading=leading, textColor=color, spaceAfter=0, alignment=align,
    )
    p = Paragraph(escape(str(value or "")).replace("\n", "<br/>"), style)
    _, height = p.wrap(width, PAGE_H)
    p.drawOn(c, x, top - height)
    return height


def _background(c, lab: str, page: int, simulation: bool, release: dict):
    c.setFillColor(colors.HexColor("#f6fafc"))
    c.rect(0, PAGE_H - 172, PAGE_W, 172, stroke=0, fill=1)
    draw_watermark(c, lab)
    if page == 1:
        draw_brand(c, lab)
    else:
        c.setFont("Metrology-Bold", 12)
        c.setFillColor(NAVY)
        c.drawString(LEFT, PAGE_H - 53, "RESULT CERTIFICATE")
        c.setFont("Metrology", 8)
        c.drawRightString(RIGHT, PAGE_H - 52, "CONTINUED")
    draw_rule(c, PAGE_H - 113)
    c.setStrokeColor(RULE)
    c.setLineWidth(.7)
    c.line(LEFT, 44, RIGHT, 44)
    c.setFillColor(MUTED)
    c.setFont("Metrology", 6.5)
    c.drawString(LEFT, 31, f"Release ID: {release['id']}")
    c.drawRightString(RIGHT, 31, f"Page {page}")
    if simulation:
        c.setFont("Metrology-Bold", 7)
        c.setFillColor(ORANGE)
        c.drawCentredString(PAGE_W / 2, 17, "DEMONSTRATION ONLY - NOT A LABORATORY CERTIFICATE")


def _table_header(c, top):
    c.setFillColor(PALE)
    c.rect(LEFT, top - 35, WIDTH, 35, stroke=0, fill=1)
    widths = (172, 48, 71, 69, 76, 71)
    labels = ("Sample ID", "Analysis", "δ¹³C / ‰", "U / ‰", "δ¹⁸O / ‰", "U / ‰")
    x = LEFT
    for index, (label, width) in enumerate(zip(labels, widths)):
        _paragraph(c, label, x + (0 if index == 0 else 3), top - 10, width - 5,
                   size=7, leading=9, bold=True, color=MUTED)
        x += width
    c.setStrokeColor(RULE)
    c.line(LEFT, top - 39, RIGHT, top - 39)
    return top - 45


def build_client_certificate(snapshot: dict) -> bytes:
    release = snapshot["release"]
    method = release["evaluation"]["method_snapshot"]
    config = method["config"]
    session = release.get("results_session_snapshot") or {}
    lab = config.get("laboratory") or DEFAULT_LAB
    simulation = bool(snapshot.get("simulation") or release.get("simulation"))
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4, invariant=1,
                      pageCompression=1)
    c.setTitle("Isotope result certificate")
    c.setAuthor(lab)
    page = 1
    _background(c, lab, page, simulation, release)
    _paragraph(c, "RESULT CERTIFICATE", LEFT, PAGE_H - 130, 350,
               size=25, leading=30, bold=True)
    _paragraph(c, "Certificate number", RIGHT - 157, PAGE_H - 132, 155,
               size=8, leading=11, bold=True, color=MUTED)
    _paragraph(c, release["id"], RIGHT - 157, PAGE_H - 146, 157,
               size=6.8, leading=9)
    c.setStrokeColor(RULE)
    c.line(LEFT, 647, RIGHT, 647)
    _paragraph(c, "Issued to", LEFT, 623, 225, size=9, bold=True, color=MUTED)
    client = session.get("client") or "Client not recorded"
    client_h = _paragraph(c, client, LEFT, 600, 225, size=11, leading=15, bold=True)
    detail_top = 600 - client_h - 4
    if session.get("project"):
        detail_top -= _paragraph(c, session["project"], LEFT, detail_top, 225, size=8, color=MUTED)
    if session.get("name"):
        detail_top -= 3 + _paragraph(c, session["name"], LEFT, detail_top - 3, 225,
                                     size=8, color=MUTED)
    _paragraph(c, "Issued by", LEFT + 280, 623, WIDTH - 280, size=9, bold=True, color=MUTED)
    lab_h = _paragraph(c, lab, LEFT + 280, 600, WIDTH - 280,
                       size=9.4, leading=13, bold=True)
    release_top = 600 - lab_h - 8
    release_h = _paragraph(c, f"Released {_date(release['at'])}", LEFT + 280,
                           release_top, WIDTH - 280, size=8, color=MUTED)
    summary_top = min(506, detail_top - 28, release_top - release_h - 28)
    _paragraph(c, "FINAL RESULTS ON VPDB", LEFT, summary_top, WIDTH,
               size=19, leading=24, bold=True)
    _paragraph(c, f"Method {config['name']}  ·  version {method['version']}",
               LEFT, summary_top - 27, WIDTH, size=8.5, color=MUTED)
    top = summary_top - 59
    top = _table_header(c, top)
    widths = (172, 48, 71, 69, 76, 71)
    for result in release["results"]:
        a = result["isotopes"]["d13c"]
        b = result["isotopes"]["d18o"]
        sample = result["label"] + (" / " + result["comment"] if result.get("comment") else "")
        values = (sample, result["source_index"], a["value"],
                  a["budget"]["expanded_uncertainty"], b["value"],
                  b["budget"]["expanded_uncertainty"])
        formatted = [str(values[0]), str(values[1])] + ["Not available" if v is None else f"{v:.4f}" for v in values[2:]]
        style = ParagraphStyle("row", fontName="Metrology", fontSize=7.3, leading=10, textColor=NAVY)
        p = Paragraph(escape(formatted[0]), style)
        _, sample_h = p.wrap(widths[0] - 9, PAGE_H)
        row_h = max(28, sample_h + 10)
        if top - row_h < 119:
            c.showPage()
            page += 1
            _background(c, lab, page, simulation, release)
            top = _table_header(c, PAGE_H - 134)
        p.drawOn(c, LEFT, top - 5 - sample_h)
        x = LEFT + widths[0]
        for value, width in zip(formatted[1:], widths[1:]):
            _paragraph(c, value, x + 2, top - 9, width - 8, size=7.3, leading=10,
                       align=2 if x > LEFT + widths[0] else 0)
            x += width
        c.setStrokeColor(RULE)
        c.line(LEFT, top - row_h, RIGHT, top - row_h)
        top -= row_h

    notes = [
        ("Measurement details", f"Expanded uncertainty U = k × combined standard uncertainty; k = {session.get('coverage_factor', config['coverage_factor']) if session else config['coverage_factor']}. {config['coverage_rationale'] if not session or 'coverage_factor' not in session else 'Coverage factor selected for this results session.'}"),
        ("Release review", f"Released by {release['review']['actor']}. Independent QC and validated range checks passed before release."),
        ("Traceability", f"Source SHA-256: {release['raw_sha256']}"),
    ]
    if session:
        notes.append(("Results session", f"{session['id']}  ·  qualification {session['qualification_id']}"))
    for heading, body in notes:
        style = ParagraphStyle("note", fontName="Metrology", fontSize=7.6, leading=11, textColor=MUTED)
        p = Paragraph(escape(body), style)
        _, body_h = p.wrap(WIDTH, PAGE_H)
        needed = body_h + 42
        if top - needed < 70:
            c.showPage()
            page += 1
            _background(c, lab, page, simulation, release)
            top = PAGE_H - 145
        top -= 16
        heading_h = _paragraph(c, heading, LEFT, top, WIDTH, size=9, bold=True)
        top -= heading_h + 5
        p.drawOn(c, LEFT, top - body_h)
        top -= body_h + 18
    c.save()
    return buffer.getvalue()
