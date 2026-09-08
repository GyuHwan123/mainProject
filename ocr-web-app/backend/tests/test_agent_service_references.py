from types import SimpleNamespace

from app.services import agent_service


def test_follow_up_pronoun_uses_schedule_mentioned_in_history(monkeypatch):
    events = [
        SimpleNamespace(title="다른 일정", date="2026-09-03"),
        SimpleNamespace(title="구글캘린더 추가", date="2026-09-03"),
    ]
    monkeypatch.setattr(agent_service.dashboard_service, "briefing", lambda _email: SimpleNamespace(events=events))
    proposals = [{"type": "task", "payload": {"title": "제목 없음"}}]

    applied = agent_service._apply_reference_to_task_proposals(
        "user@example.com",
        "그거 할 일에 넣어줘",
        [{"role": "assistant", "content": "16:00부터 구글캘린더 추가 작업이 있습니다."}],
        proposals,
    )

    assert applied is True
    assert proposals == [{
        "type": "task",
        "payload": {
            "title": "구글캘린더 추가",
            "assignee": "담당자 미정",
            "due": "2026-09-03",
            "priority": "NORMAL",
        },
    }]


def test_titleless_task_proposal_requires_clarification():
    proposals = []

    result = agent_service._tool("user@example.com", "create_task", {}, proposals)

    assert result == {"requires_clarification": True, "missing": ["title"]}
    assert proposals == []


def test_recent_meeting_can_be_prepared_as_today_schedule(monkeypatch):
    meeting = SimpleNamespace(
        title="검색 품질 개선 회의",
        meetingAt="2026-09-08T11:00:00+09:00",
        summary="진행 현황과 다음 액션 아이템을 합의했습니다.",
    )
    monkeypatch.setattr(agent_service.dashboard_service, "list_meetings", lambda _email: [meeting])

    answer, used, proposals = agent_service._direct_dashboard_response(
        "user@example.com", "최근 회의 오늘 일정에 반영해줘", [],
    )

    assert "일정으로 준비" in answer
    assert used == ["get_recent_meetings"]
    assert proposals[0]["type"] == "schedule"
    assert proposals[0]["payload"]["title"] == meeting.title
    assert proposals[0]["payload"]["date"] == "2026-09-08"


def test_recent_meeting_task_request_is_not_downgraded_to_summary(monkeypatch):
    monkeypatch.setattr(agent_service.dashboard_service, "list_meetings", lambda _email: [])

    result = agent_service._direct_dashboard_response(
        "user@example.com", "최근 회의 내용을 TASK에 반영해줘", [],
    )

    assert result is None
