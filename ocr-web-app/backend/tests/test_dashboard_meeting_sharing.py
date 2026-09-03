from app.schemas.dashboard import MeetingCreate
from app.services.dashboard_service import DashboardService


def meeting_row():
    return {
        "id": "meeting-1",
        "created_by": "owner-1",
        "meeting_at": "2026-09-03T07:00:00+00:00",
        "title": "공유 회의",
        "summary": "공유 권한 테스트",
        "content": "회의 내용",
        "status": "CONFIRMED",
    }


def test_owner_can_edit_delete_and_share():
    meeting = DashboardService()._meeting(meeting_row(), [], [], "owner-1")

    assert meeting.accessLevel == "OWNER"
    assert meeting.canEdit is True
    assert meeting.canDelete is True


def test_accepted_editor_can_edit_but_not_delete():
    participant = {
        "meeting_id": "meeting-1",
        "user_id": "editor-1",
        "display_name": "편집 참여자",
        "permission": "EDITOR",
        "invitation_status": "ACCEPTED",
    }

    meeting = DashboardService()._meeting(meeting_row(), [participant], [], "editor-1")

    assert meeting.accessLevel == "EDITOR"
    assert meeting.canEdit is True
    assert meeting.canDelete is False


def test_viewer_is_read_only():
    participant = {
        "meeting_id": "meeting-1",
        "user_id": "viewer-1",
        "display_name": "열람 참여자",
        "permission": "VIEWER",
        "invitation_status": "ACCEPTED",
    }

    meeting = DashboardService()._meeting(meeting_row(), [participant], [], "viewer-1")

    assert meeting.accessLevel == "VIEWER"
    assert meeting.canEdit is False
    assert meeting.canDelete is False


def test_meeting_create_accepts_selected_company_user_ids():
    payload = MeetingCreate(
        title="신규 회의",
        meetingAt="2026-09-03T16:00:00+09:00",
        participantUserIds=["00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002"],
    )

    assert [str(value) for value in payload.participantUserIds] == [
        "00000000-0000-0000-0000-000000000001",
        "00000000-0000-0000-0000-000000000002",
    ]
