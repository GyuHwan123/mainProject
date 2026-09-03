from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
from fastapi import HTTPException

from app.services.supabase_service import supabase_service


KST = ZoneInfo("Asia/Seoul")


class GoogleCalendarService:
    api_url = "https://www.googleapis.com/calendar/v3/calendars/primary/events"

    @staticmethod
    def _date_time(value: dict, *, end: bool = False) -> datetime:
        if value.get("dateTime"):
            return datetime.fromisoformat(value["dateTime"].replace("Z", "+00:00"))
        day = datetime.fromisoformat(value["date"]).date()
        if end:
            day -= timedelta(days=1)
            return datetime.combine(day, time(23, 59), tzinfo=KST)
        return datetime.combine(day, time.min, tzinfo=KST)

    @staticmethod
    def _utc_text(value: str | datetime) -> str:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc).isoformat()

    def import_primary(self, email: str, provider_token: str) -> int:
        now = datetime.now(timezone.utc)
        params = {
            "timeMin": (now - timedelta(days=30)).isoformat(),
            "timeMax": (now + timedelta(days=365)).isoformat(),
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": 2500,
        }
        response = httpx.get(
            self.api_url,
            params=params,
            headers={"Authorization": f"Bearer {provider_token}"},
            timeout=30,
        )
        if response.status_code in (401, 403):
            raise HTTPException(
                status_code=403,
                detail=(
                    "Google Calendar 권한이 없습니다. Google Cloud에서 Calendar API와 "
                    "calendar.readonly 범위를 활성화하고, 테스트 중이면 이 Google 계정을 테스트 사용자로 등록해 주세요."
                ),
            )
        if response.status_code >= 400:
            raise HTTPException(status_code=502, detail="Google Calendar 일정을 불러오지 못했습니다.")

        user_id = supabase_service.get_public_user_id(email)
        schedule_url = f"{supabase_service.url}/rest/v1/schedules"
        existing_response = httpx.get(
            schedule_url,
            params={"select": "title,start_at,end_at", "user_id": f"eq.{user_id}"},
            headers=supabase_service._service_headers(),
            timeout=15,
        )
        supabase_service._raise_for_supabase(existing_response, "기존 일정 조회 실패")
        existing = {
            (row["title"], self._utc_text(row["start_at"]), self._utc_text(row["end_at"]))
            for row in existing_response.json()
        }

        rows = []
        for event in response.json().get("items", []):
            if event.get("status") == "cancelled" or not event.get("summary"):
                continue
            start = self._date_time(event.get("start") or {})
            end = self._date_time(event.get("end") or {}, end=True)
            start_text, end_text = self._utc_text(start), self._utc_text(end)
            key = (event["summary"].strip(), start_text, end_text)
            if key in existing:
                continue
            description_parts = [event.get("description"), event.get("location") and f"장소: {event['location']}"]
            rows.append({
                "user_id": user_id,
                "title": event["summary"].strip()[:160],
                "description": "\n".join(part for part in description_parts if part) or None,
                "start_at": start_text,
                "end_at": end_text,
                "source": "MANUAL",
            })
            existing.add(key)
        if not rows:
            return 0
        insert = httpx.post(
            schedule_url,
            headers={**supabase_service._service_headers(), "Prefer": "return=minimal"},
            json=rows,
            timeout=30,
        )
        supabase_service._raise_for_supabase(insert, "Google Calendar 일정 저장 실패")
        return len(rows)


google_calendar_service = GoogleCalendarService()
