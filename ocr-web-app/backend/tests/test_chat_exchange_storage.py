import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException
from app.services.supabase_service import SupabaseService


class ChatExchangeStorageTests(unittest.TestCase):
    def setUp(self):
        self.service = SupabaseService()
        self.service.url = 'https://example.supabase.co'
        self.service.service_role_key = 'test-secret'

    @patch('app.services.supabase_service.httpx.post')
    def test_completed_exchange_is_one_insert_with_ordered_messages(self, post):
        post.return_value = Mock(status_code=201)
        with patch.object(self.service, 'get_chat_session') as access:
            self.service.save_chat_exchange(user_email='user@example.com', session_id='s1',
                                            question='question', answer='answer', sources=[{'id': 1}])
        access.assert_called_once_with('user@example.com', 's1')
        post.assert_called_once()
        rows = post.call_args.kwargs['json']
        self.assertEqual([row['sender'] for row in rows], ['USER', 'ASSISTANT'])
        self.assertEqual([row['message'] for row in rows], ['question', 'answer'])
        self.assertLess(rows[0]['created_at'], rows[1]['created_at'])
        self.assertEqual(rows[1]['top_k_chunks'], [{'id': 1}])

    @patch('app.services.supabase_service.httpx.post')
    def test_unauthorized_session_never_writes_messages(self, post):
        with patch.object(self.service, 'get_chat_session', side_effect=HTTPException(404)):
            with self.assertRaises(HTTPException):
                self.service.save_chat_exchange(user_email='user@example.com', session_id='s1',
                                                question='question', answer='answer', sources=[])
        post.assert_not_called()
