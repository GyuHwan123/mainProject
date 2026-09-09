import unittest
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch
from PIL import Image
from fastapi import HTTPException
from app.api.routes import finance


class ReceiptArchiveListingTests(unittest.TestCase):
    user = SimpleNamespace(email='test@example.com')

    def listing(self, **kwargs):
        return finance.receipt_archive(user=self.user, limit=2, offset=0, **kwargs)

    def test_list_does_not_wait_for_storage_and_has_next_page(self):
        rows = [{"id": str(i), "finance_records": {"merchant": "shop"}} for i in range(3)]
        with patch.object(finance.supabase_service, 'list_receipt_archive', return_value=rows), patch.object(finance.supabase_service, 'create_document_signed_url') as sign, patch.object(finance.supabase_service, 'download_document') as download:
            result = self.listing()
        self.assertEqual([row['id'] for row in result['items']], ['0', '1'])
        self.assertTrue(result['has_more'])
        self.assertEqual(result['next_offset'], 2)
        sign.assert_not_called()
        download.assert_not_called()
        self.assertNotIn('finance_records', result['items'][0])

    def test_database_failure_is_not_converted_to_empty_history(self):
        with patch.object(finance.supabase_service, 'list_receipt_archive', side_effect=RuntimeError('DB unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'DB unavailable'):
                self.listing()

    def test_duplicate_receipts_keep_raw_pagination_offset(self):
        rows = [{"id": str(i), "receipt_fingerprint": 'same'} for i in range(3)]
        with patch.object(finance.supabase_service, 'list_receipt_archive', return_value=rows):
            result = self.listing()
        self.assertEqual(len(result['items']), 1)
        self.assertEqual(result['next_offset'], 2)
        self.assertTrue(result['has_more'])

    def test_preview_requires_owned_active_record(self):
        with patch.object(finance.supabase_service, 'list_receipt_archive', return_value=[]) as query, patch.object(finance.supabase_service, 'download_document') as download:
            with self.assertRaises(HTTPException) as error:
                finance.receipt_archive_preview('other', user=self.user)
        self.assertEqual(error.exception.status_code, 404)
        query.assert_called_once_with(self.user.email, archive_id='other', limit=1)
        download.assert_not_called()

    def test_preview_is_small_and_keeps_original_url(self):
        import base64
        source = BytesIO()
        Image.new('RGB', (1600, 2400)).save(source, format='PNG')
        with patch.object(finance.supabase_service, 'list_receipt_archive', return_value=[{'source_storage_path': 'owned.png'}]), patch.object(finance.supabase_service, 'download_document', return_value=(source.getvalue(), 'image/png')), patch.object(finance.supabase_service, 'create_document_signed_url', return_value='original'):
            result = finance.receipt_archive_preview('mine', user=self.user)
        with Image.open(BytesIO(base64.b64decode(result['thumbnail_url'].split(',')[1]))) as thumbnail:
            self.assertLessEqual(thumbnail.width, 96)
            self.assertLessEqual(thumbnail.height, 128)
        self.assertEqual(result['image_url'], 'original')
