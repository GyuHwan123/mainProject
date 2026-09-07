import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.services import email_service as module


class FinanceEmailTests(unittest.TestCase):
    def test_recipient_and_excel_attachment(self):
        config = SimpleNamespace(SMTP_HOST='smtp.example.com', SMTP_PORT=587,
                                 SMTP_FROM_EMAIL='sender@example.com', SMTP_FROM_NAME='DocAI',
                                 SMTP_USERNAME='sender', SMTP_PASSWORD='test')
        with patch.object(module, 'settings', config), patch.object(module.smtplib, 'SMTP') as smtp:
            client = smtp.return_value.__enter__.return_value
            client.send_message.return_value = {}
            module.email_service.send_finance_records(author_email='user@example.com', record_count=2,
                                                       content=b'workbook-bytes', filename='finance.xlsx')
            message = client.send_message.call_args.args[0]
            self.assertEqual(message['To'], 'docai0914@gmail.com')
            self.assertEqual(message['Reply-To'], 'user@example.com')
            attachment, = list(message.iter_attachments())
            self.assertEqual(attachment.get_filename(), 'finance.xlsx')
            self.assertEqual(attachment.get_payload(decode=True), b'workbook-bytes')
            client.starttls.assert_called_once()
