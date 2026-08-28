from fastapi import APIRouter,Depends,Response,status
from app.api.routes.auth import require_current_user
from app.models.user import User
from app.schemas.dashboard import Briefing,Meeting,MeetingCreate,MeetingUpdate,Schedule,ScheduleCreate,ScheduleUpdate,Task,TaskCreate,TaskUpdate
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

@router.get("/briefing",response_model=Briefing)
def get_briefing(user:User=Depends(require_current_user)):return dashboard_service.briefing(user.email)
