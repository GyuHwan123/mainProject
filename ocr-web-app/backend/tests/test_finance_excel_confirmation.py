import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from app.api.routes import finance
from app.models.finance_receipt import FinanceExportRequest, FinanceRecordUpdate


class ExcelConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.user = SimpleNamespace(email='test@example.com', name='Test')

    def test_list_excludes_legacy_and_drafts(self):
        records = [
            {'id': 'old', 'status': 'CONFIRMED', 'structured_data': {}},
            {'id': 'draft', 'status': 'REVIEW', 'structured_data': {}},
            {'id': 'saved', 'status': 'CONFIRMED', 'structured_data': {'excel_saved_at': '2026-09-06'}},
        ]
        with patch.object(finance.supabase_service, 'list_finance_records', return_value=records):
            self.assertEqual(finance.list_records(self.user), [records[2]])

    def test_only_confirmation_adds_excel_membership(self):
        for status in ('REVIEW', 'CONFIRMED'):
            with self.subTest(status=status):
                payload = FinanceRecordUpdate(document_type='WELFARE_BENEFIT', expense_category='test', status=status)
                current = {'id': 'one', 'structured_data': {'excel_saved_at': 'old'}}
                with patch.object(finance, 'validate_classification', return_value=('WELFARE_BENEFIT', 'test', False, None)), patch.object(finance.supabase_service, 'list_finance_records', return_value=[current]), patch.object(finance.supabase_service, 'update_finance_record', side_effect=lambda email, record_id, values: values):
                    result = finance.update_record('one', payload, self.user)
                self.assertEqual('excel_saved_at' in result['structured_data'], status == 'CONFIRMED')

    def test_export_rejects_legacy_record(self):
        record = {'id': 'old', 'status': 'CONFIRMED', 'structured_data': {}}
        with patch.object(finance.supabase_service, 'list_finance_records', return_value=[record]):
            with self.assertRaises(HTTPException) as caught:
                finance.export_selected_records(FinanceExportRequest(record_ids=['old']), self.user)
        self.assertEqual(caught.exception.status_code, 422)

    def test_submit_all_skips_submitted_and_unconfirmed_records(self):
        records = [
            {'id': 'draft', 'status': 'REVIEW', 'structured_data': {}},
            {'id': 'new', 'status': 'CONFIRMED', 'structured_data': {'excel_saved_at': 'today'}},
            {'id': 'sent', 'status': 'CONFIRMED', 'structured_data': {'excel_saved_at': 'today', 'finance_workflow': {'submitted_at': 'yesterday', 'finance_confirmed_at': 'today'}}},
        ]
        with patch.object(finance.supabase_service, 'list_finance_records', return_value=records), patch.object(finance.supabase_service, 'update_finance_record', return_value=records[1]) as update:
            result = finance.submit_all_to_finance(self.user)
        self.assertEqual(len(result), 2)
        self.assertEqual(update.call_count, 1)
        self.assertEqual(update.call_args.args[:2], (self.user.email, 'new'))
        self.assertEqual(result[1]['structured_data']['finance_workflow']['finance_confirmed_at'], 'today')

    def test_preview_uses_export_workbook_and_rejects_unknown_ids(self):
        from io import BytesIO
        from openpyxl import Workbook
        workbook = Workbook()
        workbook.active.append(['test', 0, '=SUM(B2:B3)'])
        output = BytesIO()
        workbook.save(output)
        records = [{'id': 'saved', 'status': 'CONFIRMED', 'structured_data': {'excel_saved_at': 'today'}}]
        with patch.object(finance.supabase_service, 'list_finance_records', return_value=records), patch.object(finance, 'build_finance_workbook', return_value=output.getvalue()) as build:
            result = finance.preview_records(FinanceExportRequest(record_ids=['saved']), self.user)
            self.assertEqual(result['sheets'][0]['rows'][0], ('test', 0, '=SUM(B2:B3)'))
            self.assertEqual(build.call_args.args[0], records)
            with self.assertRaises(HTTPException):
                finance.preview_records(FinanceExportRequest(record_ids=['other-user']), self.user)
