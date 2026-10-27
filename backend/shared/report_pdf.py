"""One deterministic PDF layout for the worker, authenticated download and mail."""
from html import escape
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle


MAX_PDF_BYTES = 2 * 1024 * 1024
LABELS = {"available": "Available", "missing": "No data", "stale": "Stale", "incomplete": "Partial", "unavailable": "Unavailable"}
SOURCES = {"host_metrics": "Host", "events": "Events", "postgresql": "PostgreSQL"}
SEVERITIES = {"critical": "Critical", "high": "High", "medium": "Medium", "low": "Low", "info": "Info", "normal": "Normal"}


def text(value):
    # Core PDF fonts support Spanish and Western punctuation. Normalize unsupported
    # glyphs instead of silently rendering missing-character squares.
    value = str(value).replace("\u2011", "-").replace("\u2013", "-").replace("\u2014", "-")
    return escape(value.encode("cp1252", errors="replace").decode("cp1252")).replace("\n", "<br/>")


def render_pdf(report):
    output = BytesIO()
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="ReportTitle", fontName="Helvetica-Bold", fontSize=23, leading=28, textColor=colors.HexColor("#182230"), spaceAfter=12))
    styles.add(ParagraphStyle(name="ReportBody", fontName="Helvetica", fontSize=10, leading=15, textColor=colors.HexColor("#182230"), spaceAfter=10, splitLongWords=True))
    styles.add(ParagraphStyle(name="ReportSmall", parent=styles["ReportBody"], fontSize=8, leading=12, textColor=colors.HexColor("#526071")))
    styles.add(ParagraphStyle(name="ReportSection", parent=styles["ReportBody"], fontName="Helvetica-Bold", fontSize=14, leading=19, spaceBefore=16, spaceAfter=10, keepWithNext=True))
    styles.add(ParagraphStyle(name="ReportItem", parent=styles["ReportBody"], fontName="Helvetica-Bold", keepWithNext=True, spaceBefore=6))
    p = lambda value, style="ReportBody": Paragraph(text(value), styles[style])
    story = [p("GLORIFY / CONTROL", "ReportSmall"), p("Daily operational report", "ReportTitle"),
             p(f"{report['system_id']}  |  {report['report_date']}", "ReportItem"),
             p(f"Timezone: {report['timezone']}\nGenerated: {report['generated_at']}", "ReportSmall"),
             p("Summary", "ReportSection"), p(report["analysis"]["summary"]),
             p("Source coverage", "ReportSection")]
    data = [[p("Source", "ReportItem"), p("State", "ReportItem"), p("Records", "ReportItem")]]
    for source, status in report.get("source_status", {}).items():
        label = LABELS.get(status["status"], status["status"])
        issues = [LABELS.get(i, i) for i in status.get("issues", []) if i != status["status"]]
        data.append([p(SOURCES.get(source, source)), p(label + (" / " + ", ".join(issues) if issues else "")), p(status.get("count", "-"))])
    table = Table(data, colWidths=[49 * mm, 83 * mm, 34 * mm], repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#edf2ff")), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                               ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                               ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                               ("LINEBELOW", (0, 0), (-1, -1), .5, colors.HexColor("#e2e6ec"))]))
    story.extend([table, Spacer(1, 8), p("Events describe activity; a quiet day does not prove full coverage. PostgreSQL is a point-in-time capture; cumulative counters are not daily deltas.", "ReportSmall")])
    coverage = report.get("source_status", {}).get("host_metrics", {}).get("coverage")
    if coverage:
        story.append(p(f"Host coverage: {coverage['percentage']}% ({coverage['present_intervals']}/{coverage['expected_intervals']} intervals of {coverage['expected_interval_seconds']} s). Largest gap: {coverage['largest_gap_seconds']} s. Coverage {'sufficient' if coverage['sufficient'] else 'insufficient'}." + (" Truncated input: coverage is a lower bound from retained observations." if coverage.get("is_lower_bound") else ""), "ReportSmall"))
    for label, items in [("Findings", report["analysis"].get("findings", [])), ("Recommendations", report["analysis"].get("recommendations", []))]:
        story.append(p(label, "ReportSection"))
        if not items:
            story.append(p("No items reported."))
        for item in items:
            priority = item.get("severity", item.get("priority", "info"))
            title = item.get("title", item.get("action", ""))
            story.append(p(f"{SEVERITIES.get(priority, priority)} - {title}", "ReportItem"))
            if item.get("description"):
                story.append(p(item["description"]))
            story.append(p("Evidence: " + ", ".join(item.get("evidence_refs", [])), "ReportSmall"))
    def page_footer(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#e2e6ec")); canvas.line(22 * mm, 19 * mm, 188 * mm, 19 * mm)
        canvas.setFont("Helvetica", 8); canvas.setFillColor(colors.HexColor("#526071"))
        canvas.drawString(22 * mm, 14 * mm, "Glorify Control - Assisted report; review before acting")
        canvas.drawRightString(188 * mm, 14 * mm, str(doc.page)); canvas.restoreState()
    doc = SimpleDocTemplate(output, pagesize=(210 * mm, 297 * mm), leftMargin=22 * mm, rightMargin=22 * mm,
                            topMargin=22 * mm, bottomMargin=28 * mm, title=f"Glorify Control - {report['report_date']}", author="Glorify Control")
    doc.build(story, onFirstPage=page_footer, onLaterPages=page_footer)
    raw = output.getvalue()
    if len(raw) > MAX_PDF_BYTES:
        raise RuntimeError("Generated PDF exceeds the attachment limit")
    return raw
