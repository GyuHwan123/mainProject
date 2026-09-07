import unittest
from types import SimpleNamespace
from unittest.mock import patch
from fastapi import HTTPException
from app.api.routes import finance
from app.models.finance_receipt import FinanceExportRequest


class FinanceSoftDeleteTests(unittest.TestCase):
    def test_only_owned_sent_records_can_be_deleted(self):
        user = SimpleNamespace(email='test@example.com')
        for records, status in [([], 404), ([{'id': 'one', 'structured_data': {}}], 422)]:
            with patch.object(finance.supabase_service, 'list_finance_records', return_value=records), patch.object(finance.supabase_service, 'soft_delete_finance_records') as delete:
                with self.assertRaises(HTTPException) as error:
                    finance.soft_delete_sent_records(FinanceExportRequest(record_ids=['one']), user)
                self.assertEqual(error.exception.status_code, status)
                delete.assert_not_called()

    def test_sent_record_is_soft_deleted(self):
        user = SimpleNamespace(email='test@example.com')
        records = [{'id': 'one', 'structured_data': {'finance_workflow': {'submitted_at': 'today'}}}]
        with patch.object(finance.supabase_service, 'list_finance_records', return_value=records), patch.object(finance.supabase_service, 'soft_delete_finance_records', return_value=1) as delete:
            self.assertEqual(finance.soft_delete_sent_records(FinanceExportRequest(record_ids=['one']), user), {'deleted_count': 1})
            delete.assert_called_once_with(user.email, ['one'])
