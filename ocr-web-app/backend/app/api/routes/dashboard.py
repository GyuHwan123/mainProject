from fastapi import APIRouter,Depends,Response,status
from app.api.routes.auth import require_current_user
from app.models.user import User
from app.schemas.dashboard import Briefing,GoogleCalendarImportRequest,Meeting,MeetingCreate,MeetingInvitationResponse,MeetingShare,MeetingShareInvite,MeetingUpdate,ParticipantSuggestion,Schedule,ScheduleCreate,ScheduleUpdate,Task,TaskCreate,TaskUpdate
from app.services.google_calendar_service import google_calendar_service
from app.services.dashboard_service import dashboard_service
router=APIRouter()

@router.get("/schedules",response_model=list[Schedule])
def list_schedules(user:User=Depends(require_current_user)):return dashboard_service.list_schedules(user.email)
@router.post("/schedules",response_model=Schedule,status_code=status.HTTP_201_CREATED)
def create_schedule(payload:ScheduleCreate,user:User=Depends(require_current_user)):return dashboard_service.create_schedule(user.email,payload)
@router.put("/schedules/{item_id}",response_model=Schedule)
def update_schedule(item_id:str,payload:ScheduleUpdate,user:User=Depends(require_current_user)):return dashboard_service.update_schedule(user.email,item_id,payload)
@router.delete("/schedules/{item_id}",status_code=status.HTTP_204_NO_CONTENT)
def delete_schedule(item_id:str,user:User=Depends(require_current_user)):dashboard_service.delete_schedule(user.email,item_id);return Response(status_code=204)
@router.post("/calendar/google/import")
def import_google_calendar(payload:GoogleCalendarImportRequest,user:User=Depends(require_current_user)):
    return {"imported":google_calendar_service.import_primary(user.email,payload.provider_access_token)}

@router.get("/tasks",response_model=list[Task])
def list_tasks(user:User=Depends(require_current_user)):return dashboard_service.list_tasks(user.email)
@router.post("/tasks",response_model=Task,status_code=status.HTTP_201_CREATED)
def create_task(payload:TaskCreate,user:User=Depends(require_current_user)):return dashboard_service.create_task(user.email,payload)
@router.put("/tasks/{item_id}",response_model=Task)
def update_task(item_id:str,payload:TaskUpdate,user:User=Depends(require_current_user)):return dashboard_service.update_task(user.email,item_id,payload)
@router.delete("/tasks/{item_id}",status_code=status.HTTP_204_NO_CONTENT)
def delete_task(item_id:str,user:User=Depends(require_current_user)):dashboard_service.delete_task(user.email,item_id);return Response(status_code=204)

@router.get("/meetings",response_model=list[Meeting])
def list_meetings(user:User=Depends(require_current_user)):return dashboard_service.list_meetings(user.email)
@router.post("/meetings",response_model=Meeting,status_code=status.HTTP_201_CREATED)
def create_meeting(payload:MeetingCreate,user:User=Depends(require_current_user)):return dashboard_service.create_meeting(user.email,payload)
@router.put("/meetings/{item_id}",response_model=Meeting)
def update_meeting(item_id:str,payload:MeetingUpdate,user:User=Depends(require_current_user)):return dashboard_service.update_meeting(user.email,item_id,payload)
@router.delete("/meetings/{item_id}",status_code=status.HTTP_204_NO_CONTENT)
def delete_meeting(item_id:str,user:User=Depends(require_current_user)):dashboard_service.delete_meeting(user.email,item_id);return Response(status_code=204)
@router.get("/participant-suggestions",response_model=list[ParticipantSuggestion])
def participant_suggestions(q:str="",user:User=Depends(require_current_user)):return dashboard_service.participant_suggestions(user.email,q)
@router.get("/meeting-invitations",response_model=list[MeetingShare])
def meeting_invitations(user:User=Depends(require_current_user)):return dashboard_service.list_invitations(user.email)
@router.get("/meetings/{item_id}/shares",response_model=list[MeetingShare])
def meeting_shares(item_id:str,user:User=Depends(require_current_user)):return dashboard_service.list_meeting_shares(user.email,item_id)
@router.post("/meetings/{item_id}/shares",response_model=MeetingShare,status_code=status.HTTP_201_CREATED)
def invite_meeting_share(item_id:str,payload:MeetingShareInvite,user:User=Depends(require_current_user)):return dashboard_service.invite_meeting_share(user.email,item_id,payload)
@router.delete("/meetings/{item_id}/shares/{share_id}",status_code=status.HTTP_204_NO_CONTENT)
def delete_meeting_share(item_id:str,share_id:str,user:User=Depends(require_current_user)):dashboard_service.delete_meeting_share(user.email,item_id,share_id);return Response(status_code=204)
@router.post("/meetings/{item_id}/shares/respond",response_model=MeetingShare)
def respond_meeting_share(item_id:str,payload:MeetingInvitationResponse,user:User=Depends(require_current_user)):return dashboard_service.respond_meeting_share(user.email,item_id,payload.status)

@router.get("/briefing",response_model=Briefing)
def get_briefing(user:User=Depends(require_current_user)):return dashboard_service.briefing(user.email)
