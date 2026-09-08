from __future__ import annotations

import smtplib
from email.message import EmailMessage
from html import escape

from app.core.config import settings


class EmailService:
    def send_knowledge_scraps(self, *, recipient: str, sender_email: str, subject: str, note: str, scraps: list[dict], pdf_content: bytes) -> None:
        if not settings.SMTP_HOST or not settings.SMTP_FROM_EMAIL:
            raise RuntimeError("SMTP settings are incomplete")
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = f"{settings.SMTP_FROM_NAME} <{settings.SMTP_FROM_EMAIL}>"
        message["To"] = recipient
        message["Reply-To"] = sender_email
        message.set_content(f"{note}\n\nDocAI 지식 바구니 {len(scraps)}건이 공유되었습니다.")
        cards = "".join(
            f'<section style="margin:16px 0;padding:18px;border:1px solid #dce5ef;border-radius:10px">'
            f'<h2 style="margin:0 0 8px;font-size:16px;color:#213a5c">{escape(str(item.get("question") or "저장한 AI 답변"))}</h2>'
            f'<p style="margin:0 0 12px;color:#718096;font-size:12px">{escape(str(item.get("document_name") or "RAG 지식 문서"))} · 근거 {int(item.get("source_count") or 0)}개</p>'
            f'<div style="white-space:pre-wrap;line-height:1.7;color:#40546d">{escape(str(item.get("answer") or ""))}</div></section>'
            for item in scraps
        )
        message.add_alternative(
            f'<div style="max-width:680px;margin:auto;font-family:Arial,sans-serif;color:#213047">'
            f'<h1 style="color:#1767df">DocAI 지식 바구니</h1><p>{escape(note)}</p>{cards}</div>',
            subtype="html",
        )
        message.add_attachment(pdf_content, maintype="application", subtype="pdf", filename="DocAI_knowledge_report.pdf")
        smtp_class = smtplib.SMTP_SSL if settings.SMTP_PORT == 465 else smtplib.SMTP
        with smtp_class(settings.SMTP_HOST, settings.SMTP_PORT, timeout=60) as smtp:
            smtp.ehlo()
            if settings.SMTP_PORT != 465:
                smtp.starttls(); smtp.ehlo()
            if settings.SMTP_USERNAME:
                smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
            refused = smtp.send_message(message)
            if refused:
                raise smtplib.SMTPRecipientsRefused(refused)

    def send_finance_records(self, *, author_email: str, record_count: int, content: bytes, filename: str, review_url: str | None = None) -> None:
        if not settings.SMTP_HOST or not settings.SMTP_FROM_EMAIL:
            raise RuntimeError("SMTP settings are incomplete")
        message = EmailMessage()
        message["Subject"] = f"[DocAI] 최종 확정 재무 기록 {record_count}건"
        message["From"] = f"{settings.SMTP_FROM_NAME} <{settings.SMTP_FROM_EMAIL}>"
        message["To"] = "docai0914@gmail.com"
        message["Reply-To"] = author_email
        message.set_content(f"제출자: {author_email}\n최종 확정한 재무 기록 {record_count}건을 Excel 파일로 첨부합니다.")
        if review_url:
            message.set_content(f"제출자: {author_email}\n재무 기록 {record_count}건을 검토한 후 아래 링크에서 처리 완료를 눌러 주세요.\n{review_url}\n링크는 7일간 유효하며, 링크 소지자는 처리할 수 있으므로 전달하지 마세요.")
            message.add_alternative(f'<p>재무 기록 {record_count}건을 첨부했습니다.</p><p><a href="{escape(review_url, quote=True)}" style="display:inline-block;padding:14px 22px;background:#208060;color:white;border-radius:8px;text-decoration:none">재무팀 검토 및 처리</a></p><p>7일간 유효한 전용 링크입니다. 전달하지 마세요. 화면에서 처리 완료를 눌러야 반영됩니다.</p>', subtype='html')
        message.add_attachment(content, maintype="application", subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet", filename=filename)
        smtp_class = smtplib.SMTP_SSL if settings.SMTP_PORT == 465 else smtplib.SMTP
        with smtp_class(settings.SMTP_HOST, settings.SMTP_PORT, timeout=60) as smtp:
            smtp.ehlo()
            if settings.SMTP_PORT != 465:
                smtp.starttls()
                smtp.ehlo()
            if settings.SMTP_USERNAME:
                smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
            refused = smtp.send_message(message)
            if refused:
                raise smtplib.SMTPRecipientsRefused(refused)

    def send_password_reset(self, recipient: str, reset_url: str) -> None:
        if not settings.SMTP_HOST or not settings.SMTP_FROM_EMAIL:
            raise RuntimeError("SMTP settings are incomplete")

        message = EmailMessage()
        message["Subject"] = "DocAI 비밀번호 재설정"
        message["From"] = f"{settings.SMTP_FROM_NAME} <{settings.SMTP_FROM_EMAIL}>"
        message["To"] = recipient
        message.set_content(
            "비밀번호 재설정 요청이 접수되었습니다.\n\n"
            "HTML 형식으로 메일을 열어 '비밀번호 재설정하기' 버튼을 눌러 주세요.\n"
            "링크는 1시간 동안 한 번만 사용할 수 있습니다.\n\n"
            "본인이 요청하지 않았다면 이 메일을 무시해 주세요."
        )
        safe_url = escape(reset_url, quote=True)
        message.add_alternative(
            f"""<!doctype html>
<html lang="ko">
  <body style="margin:0;padding:32px;background:#f5f7fb;font-family:Arial,'Apple SD Gothic Neo','Noto Sans KR',sans-serif;color:#1f2f4a;">
    <div style="max-width:560px;margin:0 auto;padding:36px;background:#ffffff;border:1px solid #e2e8f0;border-radius:16px;">
      <h1 style="margin:0 0 18px;font-size:22px;color:#17376b;">비밀번호 재설정</h1>
      <p style="margin:0 0 10px;line-height:1.7;">비밀번호 재설정 요청이 접수되었습니다.</p>
      <p style="margin:0 0 26px;line-height:1.7;color:#64748b;">아래 버튼을 눌러 1시간 이내에 새 비밀번호를 설정해 주세요.</p>
      <a href="{safe_url}" style="display:inline-block;padding:14px 24px;background:#1769e0;color:#ffffff;text-decoration:none;border-radius:10px;font-weight:700;">비밀번호 재설정하기</a>
      <p style="margin:28px 0 0;font-size:13px;line-height:1.6;color:#94a3b8;">이 링크는 한 번만 사용할 수 있습니다. 본인이 요청하지 않았다면 이 메일을 무시해 주세요.</p>
    </div>
  </body>
</html>""",
            subtype="html",
        )

        smtp_class = smtplib.SMTP_SSL if settings.SMTP_PORT == 465 else smtplib.SMTP
        with smtp_class(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as smtp:
            smtp.ehlo()
            if settings.SMTP_PORT != 465:
                smtp.starttls()
                smtp.ehlo()
            if settings.SMTP_USERNAME:
                smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
            smtp.send_message(message)


email_service = EmailService()
