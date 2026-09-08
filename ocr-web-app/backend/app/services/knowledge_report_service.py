from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from html import escape
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

KST = ZoneInfo("Asia/Seoul")


@lru_cache(maxsize=1)
def _font() -> str:
    candidates = [
        Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf"),
        Path("C:/Windows/Fonts/malgun.ttf"),
    ]
    for path in candidates:
        if path.exists():
            pdfmetrics.registerFont(TTFont("DocAI-Korean", str(path)))
            return "DocAI-Korean"
    return "Helvetica"


def build_knowledge_report(scraps: list[dict], *, author_name: str, author_email: str) -> bytes:
    font = _font()
    generated = datetime.now(KST)
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer, pagesize=A4, rightMargin=19 * mm, leftMargin=19 * mm,
        topMargin=22 * mm, bottomMargin=20 * mm,
        title="DocAI 지식 바구니 보고서", author=author_name,
    )
    styles = getSampleStyleSheet()
    title = ParagraphStyle("CorporateTitle", parent=styles["Title"], fontName=font, fontSize=23, leading=31, textColor=colors.HexColor("#173765"), alignment=TA_CENTER)
    subtitle = ParagraphStyle("CorporateSubtitle", parent=styles["Normal"], fontName=font, fontSize=9, leading=15, textColor=colors.HexColor("#64748B"), alignment=TA_CENTER)
    heading = ParagraphStyle("Question", parent=styles["Heading2"], fontName=font, fontSize=13, leading=19, textColor=colors.HexColor("#173765"), spaceAfter=7)
    body = ParagraphStyle("Answer", parent=styles["BodyText"], fontName=font, fontSize=10, leading=17, textColor=colors.HexColor("#334155"))
    evidence = ParagraphStyle("Evidence", parent=styles["Normal"], fontName=font, fontSize=7.5, leading=12, textColor=colors.HexColor("#8492A6"), alignment=TA_RIGHT)
    story = [Paragraph("DOCUNEX · INTERNAL KNOWLEDGE REPORT", subtitle), Spacer(1, 8 * mm), Paragraph("지식 바구니 보고서", title), Spacer(1, 3 * mm), Paragraph("RAG 기반 업무 지식 요약", subtitle), Spacer(1, 12 * mm)]
    metadata = [
        ["문서 번호", f"KB-{generated:%Y%m%d-%H%M}", "작성일", generated.strftime("%Y.%m.%d")],
        ["작성자", author_name, "작성 계정", author_email],
        ["보안 등급", "사내 업무용", "수록 항목", f"{len(scraps)}건"],
    ]
    table = Table(metadata, colWidths=[25 * mm, 60 * mm, 25 * mm, 60 * mm], rowHeights=11 * mm)
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), font), ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#EEF4FB")), ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#EEF4FB")),
        ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#3E5875")), ("GRID", (0, 0), (-1, -1), .5, colors.HexColor("#D7E1EC")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.extend([table, Spacer(1, 11 * mm)])
    for index, item in enumerate(scraps, 1):
        story.append(Paragraph(f"{index:02d}. {escape(str(item.get('question') or '저장한 AI 답변'))}", heading))
        answer = escape(str(item.get("answer") or "")).replace("\n", "<br/>")
        story.append(Paragraph(answer, body))
        source = escape(str(item.get("document_name") or "RAG 지식 문서"))
        story.extend([Spacer(1, 3 * mm), Paragraph(f"근거 문서: {source} · 근거 {int(item.get('source_count') or 0)}개", evidence), Spacer(1, 7 * mm)])
        if index < len(scraps):
            separator = Table([[""]], colWidths=[172 * mm], rowHeights=[.2 * mm])
            separator.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#DCE5EF"))]))
            story.extend([separator, Spacer(1, 7 * mm)])

    def footer(canvas, doc):
        canvas.saveState(); canvas.setFont(font, 7.5); canvas.setFillColor(colors.HexColor("#8492A6"))
        canvas.drawString(19 * mm, 11 * mm, "DOCUNEX · 사내 업무용")
        canvas.drawRightString(A4[0] - 19 * mm, 11 * mm, f"{doc.page} / DocAI")
        canvas.restoreState()

    document.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
