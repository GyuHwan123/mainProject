import unittest
from unittest.mock import patch
from types import SimpleNamespace
from fastapi import HTTPException
from app.services import finance_email_review as service


class EmailReviewTests(unittest.TestCase):
    def test_create_stores_hash_not_bearer_token(self):
        with patch.object(service.settings, 'FRONTEND_URL', 'https://example.com'), patch.object(service.db, 'get_public_user_id', return_value='owner'), patch.object(service, '_request', return_value=[{'id': 'batch'}]) as request:
            batch, url = service.create_review([{'id': 'record', 'structured_data': {'excel_saved_at': 'date'}}], SimpleNamespace(email='sender@example.com'))
            token = url.split('token=')[1]
            self.assertEqual(batch, 'batch')
            stored = request.call_args.kwargs['json']
            self.assertEqual(stored['token_hash'], service.token_hash(token))
            self.assertNotIn(token, str(stored))
            self.assertEqual(stored['records'][0]['user_id'], 'owner')

    def test_invalid_or_unsent_or_expired_link_rejected(self):
        for rows in ([], [{'sent_at': None}], [{'sent_at': 'date', 'expires_at': '2000-01-01T00:00:00Z'}]):
            with patch.object(service, '_request', return_value=rows):
                with self.assertRaises(HTTPException) as error:
                    service.read_review('token')
                self.assertEqual(error.exception.status_code, 410)

    def test_inspection_does_not_mutate(self):
        row = dict(sender_email='a', recipient='b', records=[], expires_at='2099-01-01T00:00:00Z', confirmed_at=None, sent_at='today')
        with patch.object(service, '_request', return_value=[row]) as request:
            service.read_review('token')
            self.assertEqual(request.call_args.args[0], 'GET')
            request.assert_called_once()

    def test_confirmation_uses_atomic_rpc(self):
        with patch.object(service, 'read_review'), patch.object(service, '_request', return_value={'confirmed_at': 'now'}) as request:
            self.assertEqual(service.confirm_review('token'), {'confirmed_at': 'now'})
            self.assertEqual(request.call_args.args, ('POST', 'rpc/confirm_finance_email_review'))
