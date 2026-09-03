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
