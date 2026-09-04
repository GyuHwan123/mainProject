from __future__ import annotations

import smtplib
from email.message import EmailMessage
from html import escape

from app.core.config import settings


class EmailService:
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
