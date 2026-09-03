from datetime import datetime,timedelta,timezone
from zoneinfo import ZoneInfo
from typing import Any
import httpx
from fastapi import HTTPException
from app.schemas.dashboard import Briefing,Meeting,MeetingCreate,MeetingShareInvite,MeetingUpdate,Schedule,ScheduleCreate,ScheduleUpdate,Task,TaskCreate,TaskUpdate
from app.services.supabase_service import supabase_service

KST=ZoneInfo("Asia/Seoul")
class DashboardService:
    def _url(self,table:str)->str:return f"{supabase_service.url}/rest/v1/{table}"
    def _headers(self,representation:bool=False)->dict[str,str]:
        headers=supabase_service._service_headers()
        if representation:headers["Prefer"]="return=representation"
        return headers
    def _check(self,response:httpx.Response,message:str)->Any:
        supabase_service._raise_for_supabase(response,message)
        return response.json() if response.content else None
    def _user_id(self,email:str)->str:return supabase_service.get_public_user_id(email)
    def _one(self,rows:list[dict],message:str)->dict:
        if not rows:raise HTTPException(status_code=404,detail=message)
        return rows[0]
    def _schedule(self,row:dict)->Schedule:
        start=datetime.fromisoformat(row["start_at"].replace("Z","+00:00")).astimezone(KST)
        end=datetime.fromisoformat(row["end_at"].replace("Z","+00:00")).astimezone(KST)
        return Schedule(id=str(row["id"]),title=row["title"],description=row.get("description"),date=start.date().isoformat(),time=start.strftime("%H:%M"),end_time=end.strftime("%H:%M"),meetingId=row.get("meeting_id"),tone="blue")
    def _task(self,row:dict)->Task:
        due=row.get("due_at");due_text=None;urgent=False
        if due:
            value=datetime.fromisoformat(due.replace("Z","+00:00")).astimezone(KST)
            due_text=value.strftime("%m.%d");urgent=row.get("status")!="DONE" and value<=datetime.now(KST)+timedelta(days=2)
        return Task(id=str(row["id"]),title=row["title"],assignee=row["assignee_name"],assigneeId=row.get("assignee_id"),due=due_text,status=row["status"],priority=row["priority"],description=row.get("description"),meetingId=row.get("meeting_id"),progress=row["progress"],urgent=urgent)
    def _meeting(self,row:dict,participants:list[dict],tasks:list[dict],uid:str)->Meeting:
        when=datetime.fromisoformat(row["meeting_at"].replace("Z","+00:00")).astimezone(KST)
        names=[item["display_name"] for item in participants if item["meeting_id"]==row["id"] and item.get("invitation_status","ACCEPTED")!="DECLINED"]
        ids=[str(item["id"]) for item in tasks if item.get("meeting_id")==row["id"]]
        tag=f"업무 {len(ids)}건" if ids else row["status"]
        owned=str(row["created_by"])==str(uid)
        membership=next((item for item in participants if item["meeting_id"]==row["id"] and str(item.get("user_id"))==str(uid) and item.get("invitation_status","ACCEPTED")=="ACCEPTED"),None)
        permission="OWNER" if owned else (membership or {}).get("permission","VIEWER")
        return Meeting(id=str(row["id"]),date=when.strftime("%Y.%m.%d"),meetingAt=when.isoformat(),title=row["title"],participants=", ".join(names) or "참석자 없음",summary=row.get("summary") or "요약이 없습니다.",tag=tag,taskIds=ids,content=row.get("content"),status=row["status"],canEdit=owned or permission=="EDITOR",canDelete=owned,accessLevel=permission)

    def _share(self,row:dict)->dict:
        user_id=str(row["user_id"]) if row.get("user_id") else None
        return {"id":user_id or str(row.get("id") or row["meeting_id"]),"meetingId":str(row["meeting_id"]),"userId":user_id,"email":row.get("invited_email") or "","displayName":row.get("display_name") or row.get("invited_email") or "참여자","permission":row.get("permission") or "VIEWER","status":row.get("invitation_status") or "PENDING","invitedAt":row.get("invited_at")}

    def _meeting_access(self,email:str,item_id:str,edit:bool=False)->tuple[str,dict]:
        uid=self._user_id(email)
        rows=self._check(httpx.get(self._url("meetings"),params={"select":"*","id":f"eq.{item_id}"},headers=self._headers(),timeout=15),"회의록 조회 실패")
        row=self._one(rows,"회의록을 찾을 수 없습니다.")
        if str(row["created_by"])==str(uid):return uid,row
        members=self._check(httpx.get(self._url("meeting_participants"),params={"select":"*","meeting_id":f"eq.{item_id}","user_id":f"eq.{uid}"},headers=self._headers(),timeout=15),"회의록 권한 조회 실패")
        members=[item for item in members if item.get("invitation_status","ACCEPTED")=="ACCEPTED"]
        if not members or (edit and members[0].get("permission","VIEWER")!="EDITOR"):raise HTTPException(status_code=403,detail="회의록 접근 권한이 없습니다.")
        return uid,row
    def _at(self,date:str,time:str)->datetime:return datetime.fromisoformat(f"{date}T{time}:00").replace(tzinfo=KST)
    def _meeting_at(self,value:str)->str:
        parsed=datetime.fromisoformat(value)
        if parsed.tzinfo is None:parsed=parsed.replace(tzinfo=KST)
        return parsed.isoformat()
    def _due(self,value:str|None)->str|None:
        if not value:return None
        if len(value)==5:value=f"{datetime.now(KST).year}-{value.replace('.','-')}"
        return datetime.fromisoformat(value).replace(hour=23,minute=59,tzinfo=KST).isoformat()

    def list_schedules(self,email:str)->list[Schedule]:
        uid=self._user_id(email);rows=self._check(httpx.get(self._url("schedules"),params={"select":"*","user_id":f"eq.{uid}","order":"start_at.asc"},headers=self._headers(),timeout=15),"일정 조회 실패")
        return [self._schedule(row) for row in rows]
    def create_schedule(self,email:str,payload:ScheduleCreate,source:str="MANUAL")->Schedule:
        start=self._at(payload.date,payload.time);end=self._at(payload.date,payload.end_time) if payload.end_time else start+timedelta(hours=1)
        body={"user_id":self._user_id(email),"meeting_id":payload.meetingId,"title":payload.title.strip(),"description":payload.description,"start_at":start.isoformat(),"end_at":end.isoformat(),"source":source}
        rows=self._check(httpx.post(self._url("schedules"),json=body,headers=self._headers(True),timeout=15),"일정 저장 실패");return self._schedule(rows[0])
    def update_schedule(self,email:str,item_id:str,payload:ScheduleUpdate)->Schedule:
        current=self._one(self._check(httpx.get(self._url("schedules"),params={"select":"*","id":f"eq.{item_id}","user_id":f"eq.{self._user_id(email)}"},headers=self._headers(),timeout=15),"일정 조회 실패"),"일정을 찾을 수 없습니다.")
        values=payload.model_dump(exclude_unset=True);body={key:values[key] for key in ("title","description","status") if key in values}
        if "date" in values or "time" in values or "end_time" in values:
            old_start=datetime.fromisoformat(current["start_at"].replace("Z","+00:00")).astimezone(KST);date=values.get("date",old_start.date().isoformat());time=values.get("time",old_start.strftime("%H:%M"));start=self._at(date,time);body["start_at"]=start.isoformat();body["end_at"]=self._at(date,values["end_time"]).isoformat() if values.get("end_time") else (start+timedelta(hours=1)).isoformat()
        rows=self._check(httpx.patch(self._url("schedules"),params={"id":f"eq.{item_id}","user_id":f"eq.{current['user_id']}"},json=body,headers=self._headers(True),timeout=15),"일정 수정 실패");return self._schedule(self._one(rows,"일정을 찾을 수 없습니다."))
    def delete_schedule(self,email:str,item_id:str)->None:
        rows=self._check(httpx.delete(self._url("schedules"),params={"id":f"eq.{item_id}","user_id":f"eq.{self._user_id(email)}"},headers=self._headers(True),timeout=15),"일정 삭제 실패");self._one(rows,"일정을 찾을 수 없습니다.")

    def list_tasks(self,email:str)->list[Task]:
        uid=self._user_id(email);rows=self._check(httpx.get(self._url("tasks"),params={"select":"*","or":f"(owner_id.eq.{uid},assignee_id.eq.{uid})","order":"due_at.asc.nullslast"},headers=self._headers(),timeout=15),"업무 조회 실패");return [self._task(row) for row in rows]
    def create_task(self,email:str,payload:TaskCreate,source:str="MANUAL")->Task:
        status=payload.status;body={"owner_id":self._user_id(email),"assignee_id":payload.assigneeId,"assignee_name":payload.assignee.strip(),"meeting_id":payload.meetingId,"title":payload.title.strip(),"description":payload.description,"due_at":self._due(payload.due),"status":status,"priority":payload.priority,"progress":100 if status=="DONE" else 0,"completed_at":datetime.now(timezone.utc).isoformat() if status=="DONE" else None,"source":source}
        rows=self._check(httpx.post(self._url("tasks"),json=body,headers=self._headers(True),timeout=15),"업무 저장 실패");return self._task(rows[0])
    def update_task(self,email:str,item_id:str,payload:TaskUpdate)->Task:
        uid=self._user_id(email);values=payload.model_dump(exclude_unset=True);mapping={"assignee":"assignee_name","assigneeId":"assignee_id","due":"due_at"};body={mapping.get(k,k):(self._due(v) if k=="due" else v) for k,v in values.items()}
        if values.get("status")=="DONE":body.update(progress=100,completed_at=datetime.now(timezone.utc).isoformat())
        elif "status" in values:body["completed_at"]=None
        rows=self._check(httpx.patch(self._url("tasks"),params={"id":f"eq.{item_id}","or":f"(owner_id.eq.{uid},assignee_id.eq.{uid})"},json=body,headers=self._headers(True),timeout=15),"업무 수정 실패");return self._task(self._one(rows,"업무를 찾을 수 없습니다."))
    def delete_task(self,email:str,item_id:str)->None:
        rows=self._check(httpx.delete(self._url("tasks"),params={"id":f"eq.{item_id}","owner_id":f"eq.{self._user_id(email)}"},headers=self._headers(True),timeout=15),"업무 삭제 실패");self._one(rows,"업무를 찾을 수 없습니다.")

    def list_meetings(self,email:str)->list[Meeting]:
        uid=self._user_id(email);owned=self._check(httpx.get(self._url("meetings"),params={"select":"*","created_by":f"eq.{uid}","order":"meeting_at.desc"},headers=self._headers(),timeout=15),"회의록 조회 실패")
        participant_rows=self._check(httpx.get(self._url("meeting_participants"),params={"select":"*","user_id":f"eq.{uid}"},headers=self._headers(),timeout=15),"참석 회의 조회 실패");participant_ids={row["meeting_id"] for row in participant_rows if row.get("invitation_status","ACCEPTED")=="ACCEPTED"}
        shared=[]
        if participant_ids:
            shared=self._check(httpx.get(self._url("meetings"),params={"select":"*","id":f"in.({','.join(participant_ids)})","order":"meeting_at.desc"},headers=self._headers(),timeout=15),"공유 회의 조회 실패")
        rows={row["id"]:row for row in [*owned,*shared]};ids=list(rows)
        participants=[];tasks=[]
        if ids:
            encoded=f"in.({','.join(ids)})";participants=self._check(httpx.get(self._url("meeting_participants"),params={"select":"*","meeting_id":encoded},headers=self._headers(),timeout=15),"참석자 조회 실패");tasks=self._check(httpx.get(self._url("tasks"),params={"select":"id,meeting_id","meeting_id":encoded},headers=self._headers(),timeout=15),"회의 업무 조회 실패")
        return [self._meeting(row,participants,tasks,uid) for row in sorted(rows.values(),key=lambda x:x["meeting_at"],reverse=True)]
    def create_meeting(self,email:str,payload:MeetingCreate)->Meeting:
        uid=self._user_id(email);body={"created_by":uid,"title":payload.title.strip(),"meeting_at":self._meeting_at(payload.meetingAt),"content":payload.content,"summary":payload.summary,"status":payload.status}
        row=self._check(httpx.post(self._url("meetings"),json=body,headers=self._headers(True),timeout=15),"회의록 저장 실패")[0]
        names=list(dict.fromkeys([*payload.participants]))
        participant_rows=[{"meeting_id":row["id"],"display_name":name,"role":"ATTENDEE"} for name in names]
        selected_ids=list(dict.fromkeys(str(value) for value in payload.participantUserIds if str(value)!=uid))
        if selected_ids:
            users=self._check(httpx.get(self._url("users"),params={"select":"id,email,name","id":f"in.({','.join(selected_ids)})"},headers=self._headers(),timeout=15),"공유 참여자 조회 실패")
            found={str(user["id"]) for user in users}
            if found!=set(selected_ids):raise HTTPException(status_code=400,detail="선택한 참여자 중 가입되지 않은 사용자가 있습니다.")
            now=datetime.now(timezone.utc).isoformat()
            participant_rows.extend({"meeting_id":row["id"],"user_id":user["id"],"display_name":user.get("name") or user["email"],"role":"ATTENDEE","invited_email":user["email"],"permission":"VIEWER","invitation_status":"ACCEPTED","invited_by":uid,"invited_at":now,"accepted_at":now} for user in users)
        if participant_rows:self._check(httpx.post(self._url("meeting_participants"),json=participant_rows,headers=self._headers(True),timeout=15),"참석자 저장 실패")
        return self._meeting(row,participant_rows,[],uid)
    def update_meeting(self,email:str,item_id:str,payload:MeetingUpdate)->Meeting:
        uid,row=self._meeting_access(email,item_id,edit=True);values=payload.model_dump(exclude_unset=True);participants=values.pop("participants",None);mapping={"meetingAt":"meeting_at"};body={mapping.get(k,k):(self._meeting_at(v) if k=="meetingAt" else v) for k,v in values.items()}
        rows=self._check(httpx.patch(self._url("meetings"),params={"id":f"eq.{item_id}"},json=body,headers=self._headers(True),timeout=15),"회의록 수정 실패");row=self._one(rows,"회의록을 찾을 수 없습니다.")
        if participants is not None:
            self._check(httpx.delete(self._url("meeting_participants"),params={"meeting_id":f"eq.{item_id}","user_id":"is.null"},headers=self._headers(),timeout=15),"참석자 수정 실패")
            if participants:self._check(httpx.post(self._url("meeting_participants"),json=[{"meeting_id":item_id,"display_name":name,"role":"ATTENDEE"} for name in dict.fromkeys(participants)],headers=self._headers(True),timeout=15),"참석자 저장 실패")
        return next(item for item in self.list_meetings(email) if item.id==item_id)
    def delete_meeting(self,email:str,item_id:str)->None:
        rows=self._check(httpx.delete(self._url("meetings"),params={"id":f"eq.{item_id}","created_by":f"eq.{self._user_id(email)}"},headers=self._headers(True),timeout=15),"회의록 삭제 실패");self._one(rows,"회의록을 찾을 수 없습니다.")

    def participant_suggestions(self,email:str,q:str)->list[dict]:
        uid=self._user_id(email);term=q.strip().casefold()
        params={"select":"id,email,name","id":f"neq.{uid}","order":"name.asc","limit":"50"}
        rows=self._check(httpx.get(self._url("users"),params=params,headers=self._headers(),timeout=15),"참여자 검색 실패")
        if term:rows=[row for row in rows if term in str(row.get("email","")).casefold() or term in str(row.get("name","")).casefold()]
        rows=rows[:10]
        return [{"id":str(row["id"]),"email":row["email"],"name":row.get("name") or row["email"]} for row in rows]

    def list_meeting_shares(self,email:str,item_id:str)->list[dict]:
        self._meeting_access(email,item_id)
        rows=self._check(httpx.get(self._url("meeting_participants"),params={"select":"*","meeting_id":f"eq.{item_id}","user_id":"not.is.null","order":"invited_at.desc"},headers=self._headers(),timeout=15),"공유 목록 조회 실패")
        return [self._share(row) for row in rows]

    def list_invitations(self,email:str)->list[dict]:
        uid=self._user_id(email)
        rows=self._check(httpx.get(self._url("meeting_participants"),params={"select":"*","user_id":f"eq.{uid}","invitation_status":"eq.PENDING","order":"invited_at.desc"},headers=self._headers(),timeout=15),"회의록 초대 조회 실패")
        return [self._share(row) for row in rows]

    def invite_meeting_share(self,email:str,item_id:str,payload:MeetingShareInvite)->dict:
        uid,meeting=self._meeting_access(email,item_id)
        if str(meeting["created_by"])!=str(uid):raise HTTPException(status_code=403,detail="회의록 작성자만 공유할 수 있습니다.")
        invited_email=payload.email.strip().lower()
        users=self._check(httpx.get(self._url("users"),params={"select":"id,email,name","email":f"eq.{invited_email}","limit":"1"},headers=self._headers(),timeout=15),"참여자 조회 실패")
        if not users:raise HTTPException(status_code=404,detail="가입된 사용자를 찾을 수 없습니다. 상대방이 먼저 회원가입해야 합니다.")
        invited=users[0]
        if str(invited["id"])==str(uid):raise HTTPException(status_code=400,detail="본인은 이미 회의록 작성자입니다.")
        self._check(httpx.delete(self._url("meeting_participants"),params={"meeting_id":f"eq.{item_id}","user_id":f"eq.{invited['id']}"},headers=self._headers(),timeout=15),"기존 초대 정리 실패")
        body={"meeting_id":item_id,"user_id":invited["id"],"display_name":invited.get("name") or invited["email"],"role":"ATTENDEE","invited_email":invited["email"],"permission":payload.permission,"invitation_status":"PENDING","invited_by":uid,"invited_at":datetime.now(timezone.utc).isoformat()}
        row=self._check(httpx.post(self._url("meeting_participants"),json=body,headers=self._headers(True),timeout=15),"회의록 초대 실패")[0]
        return self._share(row)

    def delete_meeting_share(self,email:str,item_id:str,share_id:str)->None:
        uid,meeting=self._meeting_access(email,item_id)
        if str(meeting["created_by"])!=str(uid):raise HTTPException(status_code=403,detail="회의록 작성자만 공유를 취소할 수 있습니다.")
        rows=self._check(httpx.delete(self._url("meeting_participants"),params={"user_id":f"eq.{share_id}","meeting_id":f"eq.{item_id}"},headers=self._headers(True),timeout=15),"공유 취소 실패");self._one(rows,"공유 정보를 찾을 수 없습니다.")

    def respond_meeting_share(self,email:str,item_id:str,status:str)->dict:
        uid=self._user_id(email)
        rows=self._check(httpx.patch(self._url("meeting_participants"),params={"meeting_id":f"eq.{item_id}","user_id":f"eq.{uid}","invitation_status":"eq.PENDING"},json={"invitation_status":status,"accepted_at":datetime.now(timezone.utc).isoformat() if status=="ACCEPTED" else None},headers=self._headers(True),timeout=15),"초대 응답 실패")
        return self._share(self._one(rows,"대기 중인 초대를 찾을 수 없습니다."))
    def briefing(self,email:str)->Briefing:
        today=datetime.now(KST).date();events=[item for item in self.list_schedules(email) if item.date==today.isoformat()];tasks=[item for item in self.list_tasks(email) if item.status!="DONE" and item.due][:3];meetings=self.list_meetings(email)[:2];decisions=[item.summary for item in meetings];summary=f"오늘 일정 {len(events)}건, 확인할 업무 {len(tasks)}건이 있습니다."
        return Briefing(date=today.isoformat(),summary=summary,events=events,urgent_tasks=tasks,meeting_decisions=decisions)
    def save_agent_log(self,email:str,question:str,answer:str,tools:list[str],model:str,status:str="SUCCESS",error:str|None=None,latency:int|None=None)->None:
        body={"user_id":self._user_id(email),"question":question,"answer":answer,"used_tools":tools,"model_name":model,"status":status,"error_message":error,"latency_ms":latency}
        self._check(httpx.post(self._url("agent_logs"),json=body,headers=self._headers(),timeout=15),"Agent 로그 저장 실패")

dashboard_service=DashboardService()
