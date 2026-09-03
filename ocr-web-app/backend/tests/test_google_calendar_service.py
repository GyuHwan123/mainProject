from unittest import TestCase
from unittest.mock import Mock, patch

from app.services.google_calendar_service import google_calendar_service


class GoogleCalendarServiceTests(TestCase):
    def test_import_primary_saves_new_events_and_skips_duplicates(self):
        google_response = Mock(status_code=200)
        google_response.json.return_value = {"items": [
            {
                "id": "google-1", "status": "confirmed", "summary": "기존 일정",
                "start": {"dateTime": "2026-09-10T09:00:00+09:00"},
                "end": {"dateTime": "2026-09-10T10:00:00+09:00"},
            },
            {
                "id": "google-2", "status": "confirmed", "summary": "새 일정",
                "description": "캘린더 설명", "location": "회의실",
                "start": {"dateTime": "2026-09-11T13:00:00+09:00"},
                "end": {"dateTime": "2026-09-11T14:00:00+09:00"},
            },
        ]}
        existing_response = Mock(status_code=200, content=b"[]")
        existing_response.json.return_value = [{
            "title": "기존 일정", "start_at": "2026-09-10T00:00:00+00:00", "end_at": "2026-09-10T01:00:00+00:00",
        }]
        insert_response = Mock(status_code=201, content=b"")

        with patch("app.services.google_calendar_service.httpx.get", side_effect=[google_response, existing_response]), patch(
            "app.services.google_calendar_service.httpx.post", return_value=insert_response
        ) as post, patch(
            "app.services.google_calendar_service.supabase_service.get_public_user_id", return_value="user-id"
        ), patch("app.services.google_calendar_service.supabase_service._service_headers", return_value={"apikey": "test"}), patch(
            "app.services.google_calendar_service.supabase_service._raise_for_supabase"
        ):
            imported = google_calendar_service.import_primary("google@example.com", "provider-token")

        self.assertEqual(imported, 1)
        inserted = post.call_args.kwargs["json"]
        self.assertEqual(inserted[0]["title"], "새 일정")
        self.assertIn("장소: 회의실", inserted[0]["description"])
