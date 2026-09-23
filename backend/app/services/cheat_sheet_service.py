"""Personalized cheat-sheet generation and export helpers."""
import io
import re
from collections.abc import Sequence
from xml.sax.saxutils import escape

from pydantic import BaseModel, Field, field_validator
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from backend.app.services.ai_service import (
    NoteError,
    _generate_content_with_fallback,
)

PDF_RENDER_VERSION = "pdf-wrapped-v3-mastery-normalized"


class CheatSheetSection(BaseModel):
    topic: str
    mastery_score: float
    key_points: list[str] = Field(default_factory=list)
    review_prompts: list[str] = Field(default_factory=list)

    @field_validator("mastery_score", mode="before")
    @classmethod
    def normalize_mastery_score(cls, value: float) -> float:
        """Accept model percentages but store all scores as fractions."""
        score = float(value)
        if 1 < score <= 100:
            score /= 100
        return max(0.0, min(score, 1.0))


class CheatSheet(BaseModel):
    title: str
    sections: list[CheatSheetSection] = Field(default_factory=list)
    corrected_misconceptions: list[NoteError] = Field(default_factory=list)
    final_review_checklist: list[str] = Field(default_factory=list)


def _pdf_text(value: str) -> str:
    """Escape generated text for ReportLab Paragraph markup."""
    return escape(value).replace("\n", "<br/>")


def _topic_context(
    mastery: dict[str, float],
    errors: Sequence[NoteError],
    chunks: Sequence[dict[str, object]],
    max_chars: int = 12000,
) -> str:
    priority_topics = [topic for topic, _ in sorted(mastery.items(), key=lambda item: item[1])[:6]]
    terms = {
        term
        for topic in priority_topics
        for term in re.findall(r"[a-zA-Z0-9]{3,}", topic.lower())
    }
    ranked_chunks = sorted(
        chunks,
        key=lambda chunk: sum(
            str(chunk["chunk_text"]).lower().count(term)
            for term in terms
        ),
        reverse=True,
    )

    selected = []
    used_chars = 0
    for chunk in ranked_chunks:
        chunk_text = str(chunk["chunk_text"])
        if used_chars + len(chunk_text) > max_chars:
            continue
        selected.append(
            f"Section {chunk['chunk_index']}:\n{chunk_text}"
        )
        used_chars += len(chunk_text)
        if used_chars >= max_chars:
            break

    error_context = "\n".join(
        f"- {error.claimed_concept} -> {error.correction} [{error.severity}]"
        for error in errors
    )
    mastery_context = "\n".join(
        f"- {topic}: {score:.0%} mastery"
        for topic, score in sorted(mastery.items(), key=lambda item: item[1])[:6]
    )
    return (
        f"Priority mastery topics:\n{mastery_context or '- No completed mastery scores'}\n\n"
        f"Flagged misconceptions:\n{error_context or '- None recorded'}\n\n"
        f"Relevant study sections:\n{'\n\n'.join(selected) or '- No source sections available'}"
    )


def generate_cheat_sheet(
    course_name: str,
    mastery: dict[str, float],
    errors: Sequence[NoteError],
    chunks: Sequence[dict[str, object]],
    model_name: str,
) -> CheatSheet:
    """Generate a structured cheat sheet from bounded personalized context."""
    prompt = f"""
Create a personalized study cheat sheet for the course: {course_name}

Prioritize the lowest-mastery topics. For each topic, provide 3 to 5 concise key points
and 2 to 3 active-recall review prompts. Preserve the supplied corrections exactly in
corrected_misconceptions. End with a short final review checklist.
For every section, mastery_score MUST be a decimal fraction from 0.0 to 1.0, not a
percentage. For example, 25% mastery must be returned as 0.25.
Return only data matching the requested schema. Do not invent facts not supported by
these study sections or corrections.

{_topic_context(mastery, errors, chunks)}
"""
    response = _generate_content_with_fallback(
        model=model_name,
        contents=prompt,
        response_schema=CheatSheet,
    )
    return CheatSheet.model_validate_json(response.text)


def render_markdown(cheat_sheet: CheatSheet) -> str:
    lines = [f"# {cheat_sheet.title}", "", "## Priority Topics", ""]
    for section in cheat_sheet.sections:
        lines.extend([
            f"### {section.topic}",
            f"**Mastery:** {section.mastery_score:.0%}",
            "",
            "**Key points**",
        ])
        lines.extend(f"- {point}" for point in section.key_points)
        lines.extend(["", "**Review prompts**"])
        lines.extend(f"- {prompt}" for prompt in section.review_prompts)
        lines.append("")

    lines.extend(["## Corrected Misconceptions", ""])
    if cheat_sheet.corrected_misconceptions:
        for error in cheat_sheet.corrected_misconceptions:
            lines.extend([
                f"### {error.claimed_concept}",
                f"**Correction:** {error.correction}",
                f"**Severity:** {error.severity}",
                "",
            ])
    else:
        lines.append("No flagged misconceptions recorded.")
        lines.append("")

    lines.extend(["## Final Review Checklist", ""])
    lines.extend(f"- [ ] {item}" for item in cheat_sheet.final_review_checklist)
    return "\n".join(lines).strip() + "\n"


def render_pdf(cheat_sheet: CheatSheet) -> bytes:
    """Render the structured cheat sheet directly to a PDF."""
    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=0.65 * inch,
        leftMargin=0.65 * inch,
        topMargin=0.65 * inch,
        bottomMargin=0.65 * inch,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "CheatSheetTitle",
        parent=styles["Title"],
        alignment=TA_CENTER,
        textColor=colors.HexColor("#16324F"),
    )
    heading_style = ParagraphStyle(
        "CheatSheetHeading",
        parent=styles["Heading2"],
        textColor=colors.HexColor("#16324F"),
        spaceBefore=12,
    )
    body_style = styles["BodyText"]
    story = [Paragraph(_pdf_text(cheat_sheet.title), title_style), Spacer(1, 12)]

    for section in cheat_sheet.sections:
        story.append(Paragraph(_pdf_text(section.topic), heading_style))
        story.append(Paragraph(f"Mastery: {section.mastery_score:.0%}", body_style))
        story.append(Spacer(1, 4))
        rows = [[
            Paragraph("Key points", body_style),
            Paragraph("Review prompts", body_style),
        ]]
        rows.append([
            Paragraph(
                "<br/>".join(f"&#8226; {_pdf_text(point)}" for point in section.key_points) or "None",
                body_style,
            ),
            Paragraph(
                "<br/>".join(f"&#8226; {_pdf_text(prompt)}" for prompt in section.review_prompts) or "None",
                body_style,
            ),
        ])
        table = Table(rows, colWidths=[3.45 * inch, 3.45 * inch])
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DCE8F2")),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#8CA6BA")),
            ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#B7C8D5")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("PADDING", (0, 0), (-1, -1), 7),
        ]))
        story.extend([table, Spacer(1, 8)])

    story.append(Paragraph("Corrected Misconceptions", heading_style))
    for error in cheat_sheet.corrected_misconceptions:
        story.append(Paragraph(f"<b>{_pdf_text(error.claimed_concept)}</b>", body_style))
        story.append(Paragraph(f"Correction: {_pdf_text(error.correction)}", body_style))
        story.append(Paragraph(f"Severity: {_pdf_text(error.severity)}", body_style))
        story.append(Spacer(1, 5))

    story.append(Paragraph("Final Review Checklist", heading_style))
    for item in cheat_sheet.final_review_checklist:
        story.append(Paragraph(f"&#9633; {_pdf_text(item)}", body_style))

    document.build(story)
    return buffer.getvalue()
