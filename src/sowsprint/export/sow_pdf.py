"""Statement of Work rendering to PDF.

The document is generated with ReportLab's Platypus layer, which flows content across
pages automatically — necessary because a real SOW runs to many pages and clause
lengths are model-dependent.
"""

from __future__ import annotations

from pathlib import Path

from ..models import SOWDocument

# Brand palette, kept here so the PDF, the UI and the docs stay visually consistent.
INK = "#111827"
ACCENT = "#1E40AF"
MUTED = "#6B7280"
RULE = "#D1D5DB"
SURFACE = "#F3F4F6"


def render_sow_pdf(sow: SOWDocument, path: Path) -> Path:
    """Write the Statement of Work as a print-ready PDF and return the path."""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_JUSTIFY
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        KeepTogether,
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    path.parent.mkdir(parents=True, exist_ok=True)

    base = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "SowTitle", parent=base["Title"], fontSize=22, leading=27,
        textColor=colors.HexColor(INK), spaceAfter=4,
    )
    subtitle_style = ParagraphStyle(
        "SowSubtitle", parent=base["Normal"], fontSize=10, leading=14,
        textColor=colors.HexColor(MUTED), spaceAfter=2,
    )
    heading_style = ParagraphStyle(
        "SowHeading", parent=base["Heading2"], fontSize=12.5, leading=16,
        textColor=colors.HexColor(ACCENT), spaceBefore=12, spaceAfter=5,
    )
    body_style = ParagraphStyle(
        "SowBody", parent=base["BodyText"], fontSize=9.6, leading=14,
        alignment=TA_JUSTIFY, textColor=colors.HexColor(INK), spaceAfter=7,
    )
    small_style = ParagraphStyle(
        "SowSmall", parent=base["Normal"], fontSize=7.6, leading=10,
        textColor=colors.HexColor(MUTED),
    )

    document = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=sow.title,
        author=sow.vendor_name,
        subject=f"Statement of Work — {sow.client_name}",
        creator="SOWSprint.ai",
    )

    story: list = []
    story.append(Paragraph(_esc(sow.title), title_style))
    story.append(
        Paragraph(
            f"{_esc(sow.client_name)} &nbsp;·&nbsp; {_esc(sow.vendor_name)} &nbsp;·&nbsp; "
            f"Effective {sow.effective_date.isoformat()}",
            subtitle_style,
        )
    )
    story.append(
        Paragraph(
            f"Jurisdiction: <b>{_esc(sow.jurisdiction.label)}</b> &nbsp;|&nbsp; "
            f"Governing law: {_esc(sow.governing_law)}",
            subtitle_style,
        )
    )
    story.append(Spacer(1, 7 * mm))

    story.append(Paragraph("Executive summary", heading_style))
    story.append(Paragraph(_esc(sow.executive_summary) or "—", body_style))
    story.append(Spacer(1, 3 * mm))

    # ---------------------------------------------------------------- clauses
    story.append(Paragraph("Clauses", heading_style))
    for clause in sow.clauses:
        block = [
            Paragraph(f"{_esc(clause.number)}. {_esc(clause.heading)}", heading_style),
            Paragraph(_esc(clause.body) or "<i>Empty clause.</i>", body_style),
        ]
        if clause.citations:
            block.append(
                Paragraph(
                    "Evidence: " + ", ".join(_esc(citation) for citation in clause.citations),
                    small_style,
                )
            )
        block.append(Spacer(1, 3 * mm))
        # Keep the heading with at least the first lines of its body.
        story.append(KeepTogether(block[:2]))
        story.extend(block[2:])

    # ---------------------------------------------------------------- payments
    if sow.payment_schedule:
        story.append(PageBreak())
        story.append(Paragraph("Payment schedule", heading_style))
        rows = [["#", "Milestone", "Trigger", "%"]]
        for index, payment in enumerate(sow.payment_schedule, start=1):
            rows.append(
                [
                    str(index),
                    _esc(payment.name),
                    Paragraph(_esc(payment.trigger), small_style),
                    f"{payment.percentage:.1f}%",
                ]
            )
        total = sum(payment.percentage for payment in sow.payment_schedule)
        rows.append(["", "Total", "", f"{total:.1f}%"])
        table = Table(rows, colWidths=[10 * mm, 45 * mm, 90 * mm, 18 * mm], repeatRows=1)
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(SURFACE)),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor(INK)),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8.4),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("ALIGN", (3, 0), (3, -1), "RIGHT"),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor(RULE)),
                    ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        story.append(table)
        story.append(Spacer(1, 5 * mm))

    # ---------------------------------------------------------------- annexes
    for heading, items in (
        ("Compliance notes", sow.compliance_notes),
        ("Assumptions and dependencies", sow.assumptions_and_dependencies),
    ):
        if not items:
            continue
        story.append(Paragraph(heading, heading_style))
        for item in items:
            story.append(Paragraph(f"• {_esc(item)}", body_style))

    for heading, text in (
        ("Acceptance process", sow.acceptance_process),
        ("Change control", sow.change_control),
        ("Data protection", sow.data_protection),
        ("Intellectual property", sow.intellectual_property),
        ("Termination", sow.termination),
    ):
        if not text:
            continue
        story.append(Paragraph(heading, heading_style))
        story.append(Paragraph(_esc(text), body_style))

    # ---------------------------------------------------------------- signatures
    story.append(Spacer(1, 8 * mm))
    story.append(Paragraph("Signatures", heading_style))
    signature_rows = [
        ["For the Client", "For the Supplier"],
        [_esc(sow.client_name), _esc(sow.vendor_name)],
        ["Name: ____________________", "Name: ____________________"],
        ["Title: ____________________", "Title: ____________________"],
        ["Signature: ________________", "Signature: ________________"],
        ["Date: _____________________", "Date: _____________________"],
    ]
    signature = Table(signature_rows, colWidths=[83 * mm, 83 * mm])
    signature.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor(RULE)),
            ]
        )
    )
    story.append(signature)

    story.append(Spacer(1, 6 * mm))
    story.append(
        Paragraph(
            f"Generated by SOWSprint.ai on {sow.generated_at:%Y-%m-%d %H:%M} UTC · "
            f"{sow.word_count():,} words · {len(sow.clauses)} clauses · "
            f"{len(sow.retrieved_evidence_ids)} retrieved-evidence link(s)",
            small_style,
        )
    )

    document.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return path


def _footer(canvas, document) -> None:
    """Draw the page footer with a page counter."""
    from reportlab.lib import colors
    from reportlab.lib.units import mm

    canvas.saveState()
    canvas.setStrokeColor(colors.HexColor(RULE))
    canvas.setLineWidth(0.4)
    canvas.line(20 * mm, 15 * mm, 190 * mm, 15 * mm)
    canvas.setFont("Helvetica", 7.4)
    canvas.setFillColor(colors.HexColor(MUTED))
    canvas.drawString(20 * mm, 11 * mm, "SOWSprint.ai — generated Statement of Work")
    canvas.drawRightString(190 * mm, 11 * mm, f"Page {document.page}")
    canvas.restoreState()


def _esc(text: str) -> str:
    """Escape XML-significant characters for ReportLab's mini-HTML parser."""
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
