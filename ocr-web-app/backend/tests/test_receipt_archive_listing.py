import unittest
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import patch

from app.api.routes import finance


class ReceiptArchiveListingTests(unittest.TestCase):
    def test_signing_runs_concurrently_and_failed_preview_preserves_records(self):
        rows = [
            {"id": str(i), "receipt_fingerprint": str(i), "source_storage_path": str(i)}
            for i in range(3)
        ]
        barrier = Barrier(3)

        def sign(path):
            barrier.wait(timeout=3)
            if path == '1':
                raise RuntimeError('missing image')
            return 'signed:' + path

        with patch.object(finance.supabase_service, 'list_receipt_archive', return_value=rows), \
             patch.object(finance.supabase_service, 'create_document_signed_url', side_effect=sign):
            result = finance.receipt_archive(user=SimpleNamespace(email='test@example.com'))
        self.assertEqual([row['id'] for row in result], ['0', '1', '2'])
        self.assertEqual([row['image_url'] for row in result], ['signed:0', None, 'signed:2'])

    def test_database_failure_is_not_converted_to_empty_history(self):
        with patch.object(finance.supabase_service, 'list_receipt_archive', side_effect=RuntimeError('DB unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'DB unavailable'):
                finance.receipt_archive(user=SimpleNamespace(email='test@example.com'))

    def test_duplicate_receipts_are_signed_once(self):
        rows = [{"id": str(i), "receipt_fingerprint": 'same', "source_storage_path": 'image'} for i in range(2)]
        with patch.object(finance.supabase_service, 'list_receipt_archive', return_value=rows), \
             patch.object(finance.supabase_service, 'create_document_signed_url', return_value='signed') as sign:
            result = finance.receipt_archive(user=SimpleNamespace(email='test@example.com'))
        self.assertEqual(len(result), 1)
        sign.assert_called_once_with('image')
