import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from app.api.routes import finance
from app.models.finance_receipt import FinanceExportRequest, FinanceRecordUpdate


class ExcelConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.user = SimpleNamespace(email='test@example.com', name='Test')
        review = patch.object(finance, 'create_review', return_value=('batch', 'https://example.com/finance-review#token=test'))
        activation = patch.object(finance, 'activate_review')
        review.start()
        activation.start()
        self.addCleanup(review.stop)
        self.addCleanup(activation.stop)

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
        with patch.object(finance.supabase_service, 'list_finance_records', return_value=records), patch.object(finance, 'build_finance_workbook', return_value=b'xlsx'), patch.object(finance.email_service, 'send_finance_records') as send, patch.object(finance.supabase_service, 'update_finance_record', return_value=records[1]) as update:
            result = finance.submit_all_to_finance(self.user)
        self.assertEqual(len(result), 1)
        send.assert_called_once()
        self.assertEqual(send.call_args.kwargs["record_count"], 1)
        self.assertEqual(update.call_count, 1)
        self.assertEqual(update.call_args.args[:2], (self.user.email, 'new'))


    def test_preview_uses_export_workbook_and_rejects_unknown_ids(self):
        from io import BytesIO
        from openpyxl import Workbook
        workbook = Workbook()
        workbook.active.title = finance.SHEET_NAMES['WELFARE_BENEFIT']
        workbook.active.append(['test', 0, '=SUM(B2:B3)'])
        output = BytesIO()
        workbook.save(output)
        records = [{'id': 'saved', 'document_type': 'WELFARE_BENEFIT', 'status': 'CONFIRMED', 'structured_data': {'excel_saved_at': 'today'}}]
        with patch.object(finance.supabase_service, 'list_finance_records', return_value=records), patch.object(finance, 'build_finance_workbook', return_value=output.getvalue()) as build:
            result = finance.preview_records(FinanceExportRequest(record_ids=['saved']), self.user)
            self.assertEqual(result['sheets'][0]['rows'][0], ('test', 0, '=SUM(B2:B3)'))
            self.assertEqual(build.call_args.args[0], records)
            with self.assertRaises(HTTPException):
                finance.preview_records(FinanceExportRequest(record_ids=['other-user']), self.user)

    def test_preview_includes_only_selected_document_type_and_summary(self):
        for kind, name in finance.SHEET_NAMES.items():
            with self.subTest(kind=kind):
                records = [{'id': 'saved', 'document_type': kind, 'status': 'CONFIRMED',
                            'total_amount': 11000, 'structured_data': {'excel_saved_at': 'today'}}]
                with patch.object(finance.supabase_service, 'list_finance_records', return_value=records):
                    result = finance.preview_records(FinanceExportRequest(record_ids=['saved']), self.user)
                self.assertEqual([sheet['name'] for sheet in result['sheets']], [name, finance.SUMMARY_SHEET_NAME])

    def test_failed_email_preserves_pending_records(self):
        records = [{'id': 'new', 'status': 'CONFIRMED', 'structured_data': {'excel_saved_at': 'today'}}]
        with patch.object(finance.supabase_service, 'list_finance_records', return_value=records), patch.object(finance, 'build_finance_workbook', return_value=b'xlsx'), patch.object(finance.email_service, 'send_finance_records', side_effect=RuntimeError('smtp')), patch.object(finance.supabase_service, 'update_finance_record') as update:
            with self.assertRaises(HTTPException) as caught:
                finance.submit_all_to_finance(self.user)
            self.assertEqual(caught.exception.status_code, 502)
            update.assert_not_called()
            self.assertEqual(finance.list_records(self.user), records)

    def test_include_previous_resends_without_changing_original_batch(self):
        workflow = {'submitted_at': 'yesterday', 'finance_confirmed_at': 'today', 'finance_team_status': 'confirmed'}
        record = {'id': 'sent', 'status': 'CONFIRMED', 'structured_data': {'excel_saved_at': 'today', 'finance_workflow': workflow}}
        with patch.object(finance.supabase_service, 'list_finance_records', return_value=[record]), patch.object(finance, 'build_finance_workbook', return_value=b'xlsx'), patch.object(finance.email_service, 'send_finance_records') as send, patch.object(finance.supabase_service, 'update_finance_record', return_value=record) as update:
            result = finance.submit_all_to_finance(self.user, include_previous=True)
            send.assert_called_once()
            self.assertEqual(result, [record])
            saved = update.call_args.args[2]['structured_data']['finance_workflow']
            self.assertEqual(saved['submitted_at'], 'yesterday')
            self.assertEqual(saved['finance_confirmed_at'], 'today')
            self.assertIn('last_resent_at', saved)

    def test_empty_queue_does_not_send(self):
        with patch.object(finance.supabase_service, 'list_finance_records', return_value=[]), patch.object(finance.email_service, 'send_finance_records') as send:
            self.assertEqual(finance.submit_all_to_finance(self.user), [])
            send.assert_not_called()

    def test_sent_records_remain_visible_with_new_confirmations(self):
        records = [{'id': 'sent', 'status': 'CONFIRMED', 'structured_data': {'excel_saved_at': 'today', 'finance_workflow': {'submitted_at': 'today'}}}, {'id': 'new', 'status': 'CONFIRMED', 'structured_data': {'excel_saved_at': 'today'}}]
        with patch.object(finance.supabase_service, 'list_finance_records', return_value=records):
            self.assertEqual(finance.list_records(self.user), records)

    def test_sent_only_list_is_visible_but_does_not_resend(self):
        records = [{'id': 'sent', 'status': 'CONFIRMED', 'structured_data': {
            'excel_saved_at': 'today', 'finance_workflow': {'submitted_at': 'today'}}}]
        with patch.object(finance.supabase_service, 'list_finance_records', return_value=records), patch.object(finance.email_service, 'send_finance_records') as send:
            self.assertEqual(finance.list_records(self.user), records)
            self.assertEqual(finance.submit_all_to_finance(self.user), [])
            self.assertEqual(finance.submit_to_finance('sent', self.user), records[0])
            send.assert_not_called()
