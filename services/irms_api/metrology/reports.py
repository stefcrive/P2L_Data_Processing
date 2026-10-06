from __future__ import annotations

import io
import hashlib
from functools import partial
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfgen import canvas
from reportlab.platypus import Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.graphics.shapes import Drawing, Line, Circle, String

from .models import ISOTOPES
from .repository import encode
from .client_certificate import _date, build_client_certificate
from .report_theme import (
    DEFAULT_LAB, LEFT, MUTED, NAVY, ORANGE, PALE, RIGHT, RULE, TEAL, WIDTH,
    draw_report_page, register_fonts,
)


def number(value):
    return "Not available" if value is None else f"{value:.4f}"


def build_session_dossier(snapshot: dict) -> bytes:
    """Use the certificate's fonts, letterhead and tables without claiming a release."""
    register_fonts()
    styles = getSampleStyleSheet()
    for key in ("Normal", "Heading1", "Heading2"):
        styles[key].fontName = "Metrology" if key == "Normal" else "Metrology-Bold"
        styles[key].textColor = NAVY
    styles["Normal"].fontSize, styles["Normal"].leading = 8, 11
    styles["Heading2"].fontSize, styles["Heading2"].leading = 11, 15
    styles.add(ParagraphStyle("Cell", parent=styles["Normal"], fontSize=7, leading=10, wordWrap="CJK"))
    story = []
    def number(value):
        return "—" if value is None else f"{value:.4f}"
    def p(value, style="Normal"):
        return Paragraph(escape(str(value)).replace("\n", "<br/>"), styles[style])
    def heading(value):
        story.extend([p(value, "Heading2"), Spacer(1, 6)])
    def table(headers, records, widths):
        content = [[p(v, "Cell") for v in headers]] + [[p(v, "Cell") for v in row] for row in records]
        item = Table(content, colWidths=[WIDTH * w / sum(widths) for w in widths], repeatRows=1, hAlign="LEFT")
        item.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), PALE), ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LINEBELOW", (0, 0), (-1, -1), .3, RULE)]))
        story.extend([item, Spacer(1, 10)])
    session, method = snapshot["session"], snapshot["method"]
    heading(session["name"])
    story.extend([p(f"Client: {session['client']}\nProject: {session['project']}\nMethod: {method['config']['name']} / v{method['version']}\nQualification: {session['qualification_id']}"), Spacer(1, 8)])
    decisions = sorted({r["decision"] for r in snapshot["results"]})
    heading("Result status: " + ", ".join(decisions))
    story.extend([p("This calculation dossier is a release certificate only for rows explicitly marked released. Blocked and review results remain provisional. Missing uncertainties are not zero."),
                  p(session["processing_evidence"]), Spacer(1, 10)])
    if snapshot["simulation"]:
        story.extend([p("SIMULATED QUALIFICATION. See the observed/synthetic origin and calibration verification of each result."), Spacer(1, 10)])
    heading("Results and expanded uncertainty / per mille VPDB")
    groups = {name: f"G{i+1}" for i, name in enumerate(dict.fromkeys(r["sample_group"] for r in snapshot["results"]))}
    story.extend([p("; ".join(f"{code}: {name}" for name, code in groups.items())), Spacer(1, 6)])
    table(["Sample / analysis", "Group", "Carbon-13", "U (C)", "Oxygen-18", "U (O)", "Decision"],
        [[f"{r['sample']} / {r['analysis']}\n{r.get('sample_identifier') or ''}", groups[r["sample_group"]],
          number(r["d13c_value"]), number(r["d13c_U"]), number(r["d18o_value"]), number(r["d18o_U"]), r["decision"]]
         for r in snapshot["results"]], [29, 8, 14, 12, 14, 12, 16])
    heading("Calculation model and uncertainty")
    story.extend([p("z = x - c(p - p0); y = A1 + (z - M1)(A2 - A1)/(M2 - M1). Already normalized Qtegra values retain the documented normalization. Only remaining validated corrections are applied."),
        p("u_norm² = Jᵀ Σ_anchors J. u_c² = u_prec² + u_norm² + u_corr² + sum(u_j²), for independent components; U = k × u_c. u_prec is an individual QC standard deviation, not the standard error of its mean. Correction sensitivity includes its shared contribution to sample and anchors."), Spacer(1, 10)])
    for calc in snapshot["calculations"]:
        if not calc["results"]:
            continue
        heading("Evaluation " + calc["evaluation_id"][:12])
        for iso, label in (("d13c", "Carbon-13"), ("d18o", "Oxygen-18")):
            model = calc["normalization"].get(iso)
            if model:
                story.extend([p(f"{label}: slope {model['slope']:.5g}; intercept {model['intercept']:.5g}. Assigned anchors: {model['assigned']}. Measured means: {model['measured']}."), Spacer(1, 5)])
                table(["Anchor covariance", "A1", "A2", "M1", "M2"],
                    [[name, *[f"{v:.5g}" for v in values]] for name, values in zip(("A1", "A2", "M1", "M2"), model["input_covariance"])], [24, 19, 19, 19, 19])
                correction = model.get("correction")
                if correction:
                    story.extend([p(f"Residual correction: {correction['name']}. Predictor: {correction['predictor']}; slope c={correction['slope']:.5g}, u(c)={correction['u_slope']:.5g}; reference p0={correction['center']:.5g}; validated domain {correction['domain']['low']} to {correction['domain']['high']}."), Spacer(1, 6)])
            table([f"{label} / sample", "u_prec", "u_norm", "u_corr", "u_c", "k", "U"],
                [[f"{r['label']} / {r['source_index']}\n{r.get('comment') or ''}", *[number(r.get("isotopes", {}).get(iso, {}).get(key)) for key in ("u_prec", "u_norm", "u_corr")],
                  *[number(r.get("isotopes", {}).get(iso, {}).get("budget", {}).get(key)) for key in ("u_combined", "k", "expanded_uncertainty")]] for r in calc["results"]],
                [28, 13, 13, 13, 13, 8, 13])
            components = next((r["isotopes"][iso]["budget"]["components"] for r in calc["results"] if r.get("isotopes", {}).get(iso, {}).get("budget")), [])
            for component in components:
                story.append(p(f"{component['name']}: {component['rationale']}"))
    heading("QC correction validation")
    included_runs = {r["run_id"] for r in snapshot["results"]}
    for item in snapshot.get("correction_verification", []):
        if item["run_id"] not in included_runs or not item.get("review"):
            continue
        table(["Run / isotope", "Paired QC", "SD before", "SD after", "SD reduction", "Status"],
            [[item["run_id"][:12] + " / " + iso, value["paired_n"], number(value["before"]["sd"]), number(value["after"]["sd"]),
              "—" if value["sd_reduction_fraction"] is None else f"{100*value['sd_reduction_fraction']:.1f}%", value["status"]]
             for iso, value in item["review"].items()], [27, 13, 14, 14, 15, 22])
    heading("Traceability and review")
    sources = {rid: next(r for r in snapshot["results"] if r["run_id"] == rid) for rid in dict.fromkeys(r["run_id"] for r in snapshot["results"])}
    source_codes = {rid: f"R{i+1}" for i, rid in enumerate(sources)}
    table(["Source / evaluation", "Original SHA-256", "Origin / calibration transfer"],
          [[f"{source_codes[rid]} / {rid}\nEvaluation: {r['evaluation_id']}", r["raw_sha256"], f"{r['data_origin']} / {r['calibration_verification']}"] for rid, r in sources.items()], [36, 36, 28])
    issues = {issue: f"E{i+1}" for i, issue in enumerate(dict.fromkeys(issue.strip() for r in snapshot["results"] for issue in r["issues"].split(";") if issue.strip()))}
    table(["Review code", "Issue requiring review"], [[code, issue] for issue, code in issues.items()], [12, 88])
    table(["Sample / analysis", "Source", "Decision", "Review codes / accepted exceptions"],
          [[f"{r['sample']} / {r['analysis']}\n{r.get('sample_identifier') or ''}", source_codes[r["run_id"]], r["decision"],
            ", ".join(issues[i.strip()] for i in r["issues"].split(";") if i.strip()) + ("\nAccepted: " + r["accepted_exceptions"] if r.get("accepted_exceptions") else "")]
           for r in snapshot["results"]], [28, 10, 16, 46])
    for calc in snapshot["calculations"]:
        for row in calc["results"]:
            for review in row.get("reviews", []):
                story.append(p(f"Exception review for {row['label']}: {review['actor']} / {review['at']} / {review['reason']} / {', '.join(review['issues'])}"))
    story.append(p(f"Export prepared by {snapshot['review']['actor']}. {snapshot['review']['reason']}"))
    buffer = io.BytesIO()
    lab = method["config"].get("laboratory") or DEFAULT_LAB
    digest = hashlib.sha256(encode(snapshot).encode()).hexdigest()[:8].upper()
    page = partial(draw_report_page, lab=lab, title="RESULTS DOSSIER", subtitle="Isotope results and calculation certificate",
        number=f"RES-{snapshot['exported_at'][:10].replace('-', '')}-{digest}", issue_date=_date(snapshot["exported_at"]), simulation=snapshot["simulation"])
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=LEFT-6, rightMargin=A4[0]-RIGHT-6, topMargin=194, bottomMargin=62,
                            title="IRMS results and calculation dossier", author=lab)
    doc.build(story, onFirstPage=page, onLaterPages=page)
    return buffer.getvalue()


def build_report(kind: str, snapshot: dict, repository) -> bytes:
    register_fonts()
    if kind == "client":
        return build_client_certificate(snapshot)
    styles = getSampleStyleSheet()
    for name in ("Normal", "Title", "Heading1", "Heading2"):
        styles[name].fontName = "Metrology-Bold" if name != "Normal" else "Metrology"
        styles[name].textColor = NAVY
    styles["Normal"].fontSize = 9
    styles["Normal"].leading = 14
    styles["Title"].fontSize = 23
    styles["Title"].leading = 29
    styles["Heading2"].fontSize = 13
    styles["Heading2"].leading = 18
    styles["Heading2"].spaceBefore = 8
    styles["Heading2"].spaceAfter = 5
    styles.add(ParagraphStyle("Small", parent=styles["Normal"], fontSize=7.5, leading=11, wordWrap="CJK"))
    styles.add(ParagraphStyle("Muted", parent=styles["Normal"], fontSize=8, leading=12, textColor=MUTED))
    styles.add(ParagraphStyle("TableHeading", parent=styles["Small"], fontName="Metrology-Bold", textColor=MUTED))
    story = []

    def text(value, style="Normal"):
        return Paragraph(escape(str(value)).replace("\n", "<br/>"), styles[style])

    def para(value, style="Normal"):
        story.append(text(value, style))
        gap = Spacer(1, 7)
        gap.keepWithNext = style.startswith("Heading")
        story.append(gap)

    def table(headers, rows, widths=None, keep_heading=False):
        content = [[text(v, "TableHeading") for v in headers]] + [[text(v, "Small") for v in row] for row in rows]
        if widths:
            widths = [w * WIDTH / sum(widths) for w in widths]
        t = Table(content, colWidths=widths, repeatRows=1, hAlign="LEFT")
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), PALE),
            ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 7),
            ("RIGHTPADDING", (0, 0), (-1, -1), 7), ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ("LINEBELOW", (0, 0), (-1, 0), .6, RULE),
            ("LINEBELOW", (0, 1), (-1, -1), .35, RULE),
        ]))
        if keep_heading:
            heading = story[-2:]
            del story[-2:]
            story.append(KeepTogether([*heading, t]))
        else:
            story.append(t)
        story.append(Spacer(1, 12))

    titles = {"client": "Isotope result certificate", "qualification": "Metrological qualification dossier", "history": "Historical QC performance"}
    lab = snapshot.get("laboratory") or snapshot.get("method", {}).get("config", {}).get("laboratory") or DEFAULT_LAB
    intro = Table([[text("Report scope", "TableHeading"), text("Issued by", "TableHeading")],
                   [text("Historical quality control" if kind == "history" else "Method qualification", "Heading2"), text(lab, "Normal")]],
                  colWidths=[WIDTH * .52, WIDTH * .48], hAlign="LEFT")
    intro.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                              ("LEFTPADDING", (0, 0), (-1, -1), 0),
                              ("BOTTOMPADDING", (0, 1), (-1, 1), 12),
                              ("LINEBELOW", (0, 1), (-1, 1), .7, RULE)]))
    story.extend([intro, Spacer(1, 14)])
    if snapshot.get("simulation") or snapshot.get("release", {}).get("simulation"):
        para("Demonstration data and simulated approvals", "Muted")
    if kind == "qualification":
        q, method = snapshot["qualification"], snapshot["method"]
        config = method["config"]
        para(f"{config['laboratory'] or 'Laboratory not specified'} · {config['instrument']}")
        para(f"Method v{method['version']} · {q['trigger']} · {q['status'].upper()}")
        para(config["intended_use"])
        if q["status"] != "approved":
            para("Review dossier. This qualification has not been approved.", "Heading2")
        table(["Test", "Result", "Value / unit", "Criterion and review"],
              [[t["name"], t["result"], f"{number(t['value'])} {t['unit']}", t["criterion"] + "\n" + t["reason"] + "\n" + t["actor"]] for t in q["tests"]], [118, 60, 90, 216])
        para("Method conditions and validated ranges", "Heading2")
        para(f"Configuration: {config['configuration']}\nTuneBook: {config['tunebook']}\nReaction temperature: {config['reaction_temperature_c']} °C\nPreparation: {config['preparation']}\nAcquisition: {config['acquisition']}")
        table(["Quantity", "Validated interval"], [[k, f"{v['low']} to {v['high']}" if v else "Unset"] for k, v in config["ranges"].items()], [190, 294])
        para("Reference materials and carousel", "Heading2")
        for material in snapshot["materials"].values():
            para(f"{material['name']} · lot {material['lot'] or 'unset'} · certificate {material['certificate'] or 'unset'}")
            table(["Isotope", "Assigned / ‰", "Quoted uncertainty", "Type / k"], [[iso, number(material["assigned"][iso]["value"]), number(material["assigned"][iso]["uncertainty"]), f"{material['assigned'][iso]['uncertainty_type']} / {material['assigned'][iso]['k']}"] for iso in ISOTOPES], [100, 125, 125, 134])
        table(["Material lot record", "Target mass / µg", "Replicates"], [[s["material_id"], s["mass_ug"], s["replicates"]] for s in q["carousel"]], [280, 112, 92])
        para("Carousel mass matching uses a ±0.5 µg tolerance. Individual weighed masses remain in the run record.", "Small")
        para("Residual-effect decisions", "Heading2")
        for effect, review in q.get("effects", {}).items():
            para(f"{effect}: {review['decision']}\nEvidence: {review['evidence']}\n{review['reason']}\nReviewed by {review['actor']}")
        for iso, correction in config.get("corrections", {}).items():
            if correction:
                para(f"{iso} secondary correction: {correction['name']}", "Heading2")
                para(f"z = x - c(p-p0), before anchoring. Predictor: {correction['predictor']}. c={correction['slope']}; u(c)={correction['u_slope']}; p0={correction['center']}.\nValidated domain: {correction['domain']['low']} to {correction['domain']['high']}.\nTraining: {correction['training_evidence']}\nIndependent validation: {correction['validation_evidence']}\n{correction['independence_rationale']}")
        for evaluation in snapshot["evaluations"]:
            para(f"Analytical evaluation {evaluation['id']}", "Heading2")
            para("Ready for scientific review" if evaluation["ready"] else "Blocked: " + "; ".join(evaluation["blockers"]))
            for iso in ISOTOPES:
                qc = evaluation["qc"]["isotopes"][iso]
                para(f"{iso}: QC n={qc['n']}, mean={number(qc['mean'])}, SD={number(qc['sd'])}, bias={number(qc['bias'])} ‰; passed={qc['passed']}")
                model = evaluation["normalization"].get(iso)
                if model:
                    para(f"{iso} normalization: {model['formula']}\na={number(model['intercept'])}; b={number(model['slope'])}\nAnchor uncertainties include assigned values and measured means. Two means supply no residual regression uncertainty.")
                representative = next((r["isotopes"].get(iso) for r in evaluation["results"] if r["role"] == "qc" and iso in r["isotopes"]), None)
                if representative and representative.get("budget"):
                    table(["Uncertainty contribution", "Standard uncertainty / ‰", "Basis"], [[c["name"], number(c["u"]), c["rationale"]] for c in representative["budget"]["components"]], [134, 126, 224])
            for material in evaluation["diagnostics"]["materials"]:
                para(f"Diagnostics: {material['label']}", "Heading2")
                rows = []
                for iso, fits in material["isotopes"].items():
                    for effect in ("mass_dependence", "intensity_dependence", "pressure_residual", "drift", "memory"):
                        fit = fits[effect]
                        rows.append([iso, effect, fit["status"], number(fit.get("slope")), number(fit.get("slope_se")), number(fit.get("effect_span"))])
                table(["Isotope", "Effect", "Evidence", "Slope", "SE slope", "Span / ‰"], rows, [49, 108, 114, 70, 70, 73], keep_heading=True)
        para("Diagnostic assets", "Heading2")
        for asset in q["assets"]:
            para(f"{asset['filename']}\n{asset['interpretation']}\nSHA-256 {asset['sha256']}", "Small")
            if Path(asset["filename"]).suffix.lower() in (".png", ".jpg", ".jpeg"):
                try:
                    img = Image(io.BytesIO(repository.read_blob(asset["sha256"])))
                    factor = min(480 / img.imageWidth, 330 / img.imageHeight, 1)
                    img.drawWidth, img.drawHeight = img.imageWidth * factor, img.imageHeight * factor
                    story.extend([img, Spacer(1, 12)])
                except Exception:
                    para("Original asset is available in the evidence archive; no image interpretation was automated.")
        if q.get("approval"):
            para(f"Suitability approved by {q['approval']['actor']} at {q['approval']['at']}. {q['approval']['reason']}")
        para("Complete input covariance matrices, residuals, calculation records, exclusions and audit events are retained in the accompanying JSON snapshot.", "Small")
    else:
        para("Individual QC observations are grouped by method version, material lot and intervention period. Descriptive SD is not an approved intermediate-precision estimate until the period is reviewed.")
        for group in snapshot["history"]:
            group_start = len(story)
            para(f"{group['material']['name']} · method v{group['method_version']} · {group['run_count']} runs", "Heading2")
            para(f"Lot: {group['material']['lot'] or 'unset'}\n{group['configuration']} · Period: {group['period_key']}", "Muted")
            isotope_labels = {"d13c": "δ¹³C", "d18o": "δ¹⁸O"}
            table(["Isotope", "Observations", "Mean / ‰", "SD / ‰", "Control status"],
                  [[isotope_labels.get(iso, iso), stats['n'], number(stats['mean']), number(stats['sd']),
                    stats['status'].replace('_', ' ')] for iso, stats in group['isotopes'].items()],
                  [64, 83, 83, 83, 194])
            for iso, stats in group["isotopes"].items():
                values = [p["value"] for p in stats["points"]]
                if values:
                    bounds = values + (stats["limits"] or [])
                    low, high = min(bounds), max(bounds)
                    span = max(high - low, .01)
                    drawing = Drawing(WIDTH, 128)
                    ys = lambda y: 26 + 74 * (y - low + span * .1) / (span * 1.2)
                    drawing.add(String(0, 114, isotope_labels.get(iso, iso) + " / ‰", fontName="Metrology-Bold", fontSize=9, fillColor=NAVY))
                    drawing.add(Line(48, 20, WIDTH, 20, strokeColor=RULE))
                    drawing.add(String(0, ys(high) - 2, number(high), fontName="Metrology", fontSize=7, fillColor=MUTED))
                    drawing.add(String(0, ys(low) - 2, number(low), fontName="Metrology", fontSize=7, fillColor=MUTED))
                    for limit in stats["limits"] or []:
                        drawing.add(Line(48, ys(limit), WIDTH, ys(limit), strokeColor=ORANGE, strokeDashArray=[3, 3]))
                    for i, v in enumerate(values):
                        drawing.add(Circle(50 + (WIDTH - 53) * i / max(len(values) - 1, 1), ys(v), 2, fillColor=TEAL, strokeWidth=0))
                    drawing.add(String(48, 6, "Individual QC observations, acquisition order", fontName="Metrology", fontSize=7, fillColor=MUTED))
                    story.append(drawing)
            story[group_start:] = [KeepTogether(story[group_start:])]
        if snapshot["periods"]:
            para("Reviewed homogeneous periods", "Heading2")
            table(["Period", "Reviewer", "Estimator and evidence"],
                  [[period['name'], period['actor'], period['estimator'] + "\n" + period['reason']]
                   for period in snapshot['periods']], [145, 85, 277], keep_heading=True)
        if snapshot["interventions"]:
            para("Interventions and qualifications", "Heading2")
            for event in snapshot["interventions"]:
                para(f"{event['created_at']} · {event['kind']} · {event['status']} · {event['reason']}")
    buffer = io.BytesIO()
    # Platypus frames add six points of padding; align content with the header rules.
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=A4[0] - RIGHT - 6, leftMargin=LEFT - 6,
                            topMargin=194, bottomMargin=62, title=titles[kind], author=lab)
    digest = hashlib.sha256(encode(snapshot).encode("utf-8")).hexdigest()[:8].upper()
    report_number = f"{'HIS' if kind == 'history' else 'QUAL'}-{snapshot['generated_at'][:10].replace('-', '')}-{digest}"
    page = partial(draw_report_page, lab=lab,
                   title="QC HISTORY" if kind == "history" else "QUALIFICATION",
                   subtitle=titles[kind], number=report_number,
                   issue_date=_date(snapshot["generated_at"]), simulation=bool(snapshot.get("simulation")))
    doc.build(story, onFirstPage=page, onLaterPages=page,
              canvasmaker=lambda *args, **kwargs: canvas.Canvas(*args, **{**kwargs, "invariant": 1}))
    return buffer.getvalue()
