from __future__ import annotations
import json,time
from datetime import datetime
from typing import Any
import httpx
from fastapi import HTTPException
from app.core.config import settings
from app.schemas.dashboard import AgentChatResponse,MeetingExtractionResponse
from app.services.dashboard_service import dashboard_service

TOOLS=[
 {"type":"function","function":{"name":"get_today_schedules","description":"로그인 사용자의 오늘 일정을 조회한다.","parameters":{"type":"object","properties":{}}}},
 {"type":"function","function":{"name":"get_week_tasks","description":"로그인 사용자의 할 일과 마감 업무를 조회한다.","parameters":{"type":"object","properties":{}}}},
 {"type":"function","function":{"name":"get_recent_meetings","description":"로그인 사용자의 최근 회의록을 조회한다.","parameters":{"type":"object","properties":{}}}},
 {"type":"function","function":{"name":"create_schedule","description":"사용자 요청에 따라 새 일정을 생성한다.","parameters":{"type":"object","required":["title","date","time"],"properties":{"title":{"type":"string"},"date":{"type":"string","description":"YYYY-MM-DD"},"time":{"type":"string","description":"HH:MM"},"description":{"type":"string"}}}}},
 {"type":"function","function":{"name":"create_task","description":"사용자 요청에 따라 새 할 일을 생성한다.","parameters":{"type":"object","required":["title","assignee"],"properties":{"title":{"type":"string"},"assignee":{"type":"string"},"due":{"type":"string","description":"YYYY-MM-DD"},"priority":{"type":"string","enum":["LOW","NORMAL","HIGH"]}}}}},
]

REFERENCE_WORDS=("그거","그것","그 일","그 일정","방금","위 내용","위 일정")

def _history_text(history:list[dict])->str:
    return " ".join(str(item.get("content", "")) for item in history)

def _referenced_schedule(email:str,message:str,history:list[dict]):
    """Resolve short follow-up requests such as '그거 할 일에 넣어줘'."""
    if not any(word in message for word in REFERENCE_WORDS):return None
    context=_history_text(history)
    events=dashboard_service.briefing(email).events
    mentioned=[event for event in events if event.title and event.title in context]
    if mentioned:return mentioned[-1]
    return events[0] if len(events)==1 else None

def _apply_reference_to_task_proposals(email:str,message:str,history:list[dict],proposals:list[dict])->bool:
    wants_task=any(word in message for word in ("할 일","할일","업무")) and any(word in message for word in ("추가","넣어","등록"))
    if not wants_task:return False
    event=_referenced_schedule(email,message,history)
    if not event:return False
    missing_titles={"", "제목 없음", "미정", "없음"}
    proposals[:]=[
        item for item in proposals
        if item.get("type")!="task"
        or str((item.get("payload") or {}).get("title") or "").strip() not in missing_titles
    ]
    if not any(item.get("type")=="task" and (item.get("payload") or {}).get("title")==event.title for item in proposals):
        proposals.append({"type":"task","payload":{"title":event.title,"assignee":"담당자 미정","due":event.date,"priority":"NORMAL"}})
    return True

def _tool(email:str,name:str,args:dict[str,Any],proposals:list[dict])->Any:
    if name=="get_today_schedules":
        briefing=dashboard_service.briefing(email);return [item.model_dump() for item in briefing.events]
    if name=="get_week_tasks":return [item.model_dump() for item in dashboard_service.list_tasks(email) if item.status!="DONE"][:10]
    if name=="get_recent_meetings":return [item.model_dump() for item in dashboard_service.list_meetings(email)[:5]]
    if name=="create_schedule":
        proposal={"type":"schedule","payload":args};proposals.append(proposal);return {"requires_confirmation":True,**proposal}
    if name=="create_task":
        if str(args.get("title") or "").strip() in {"", "제목 없음", "미정", "없음"}:return {"requires_clarification":True,"missing":["title"]}
        proposal={"type":"task","payload":args};proposals.append(proposal);return {"requires_confirmation":True,**proposal}
    raise ValueError(f"지원하지 않는 도구: {name}")

async def chat(email:str,message:str,history:list[dict]|None=None)->AgentChatResponse:
    started=time.perf_counter();used:list[str]=[];proposals:list[dict]=[];model=settings.DASHBOARD_AGENT_MODEL
    system="당신은 사내 AI 업무 비서입니다. 반드시 제공된 도구로 로그인 사용자의 실제 일정, 업무, 회의를 조회하세요. 생성 요청은 내용을 확인한 뒤 create 도구를 사용하세요. 답변은 간결한 한국어로 작성하세요."
    safe_history=[{"role":item.get("role"),"content":str(item.get("content",""))[:2000]} for item in (history or [])[-8:] if item.get("role") in {"user","assistant"}]
    messages=[{"role":"system","content":system},*safe_history,{"role":"user","content":message}]
    try:
        async with httpx.AsyncClient(base_url=settings.OLLAMA_BASE_URL,timeout=90) as client:
            first=await client.post("/api/chat",json={"model":model,"messages":messages,"tools":TOOLS,"stream":False})
            fallback=not first.is_success
            if fallback:
                names=[item["function"]["name"] for item in TOOLS]
                planner=f"""사용자 요청을 처리할 도구를 선택하세요.
사용 가능한 도구: {names}
반드시 JSON 배열만 출력하세요. 각 항목 형식은 {{"name":"도구명","arguments":{{}}}} 입니다.
조회 질문은 관련 조회 도구를 선택하고, 생성 요청만 create 도구를 선택하세요.
이전 대화에서 '그거', '그 일', '방금 일정'이 가리키는 제목과 날짜를 찾아 arguments에 채우세요.
이전 대화: {json.dumps(safe_history,ensure_ascii=False)}
사용자 요청: {message}"""
                planned=await client.post("/api/chat",json={"model":model,"messages":[{"role":"system","content":"당신은 도구 실행 계획기입니다. 설명 없이 JSON만 출력합니다."},{"role":"user","content":planner}],"format":"json","stream":False})
                planned.raise_for_status();raw=planned.json().get("message",{}).get("content","[]");decoded=json.loads(raw)
                if isinstance(decoded,dict):decoded=decoded.get("tools") or decoded.get("calls") or [decoded]
                calls=[{"function":{"name":item.get("name"),"arguments":item.get("arguments") or {}}} for item in decoded]
                messages=[{"role":"system","content":system},*safe_history,{"role":"user","content":message}]
                assistant={}
            else:
                assistant=first.json().get("message",{});messages.append(assistant);calls=assistant.get("tool_calls") or []
            for call in calls:
                function=call.get("function",{});name=function.get("name","");arguments=function.get("arguments") or {}
                if isinstance(arguments,str):arguments=json.loads(arguments or "{}")
                result=_tool(email,name,arguments,proposals);used.append(name)
                if fallback:messages.append({"role":"user","content":f"도구 {name} 실행 결과: {json.dumps(result,ensure_ascii=False)}"})
                else:messages.append({"role":"tool","content":json.dumps(result,ensure_ascii=False),"tool_name":name})
            if calls:
                final=await client.post("/api/chat",json={"model":model,"messages":messages,"stream":False});final.raise_for_status();answer=final.json().get("message",{}).get("content","").strip()
            else:answer=(assistant.get("content") or "").strip()
        reference_task_applied=_apply_reference_to_task_proposals(email,message,safe_history,proposals)
        if reference_task_applied:
            used.append("get_today_schedules") if "get_today_schedules" not in used else None
            answer="방금 확인한 일정을 할 일로 준비했습니다. 아래 내용을 확인한 뒤 추가해 주세요."
        context_text=" ".join([item["content"] for item in safe_history]+[message])
        action_intent=any(word in message for word in ("추가","넣어","등록","Action","액션","할 일","캘린더"))
        wants_meeting_actions="회의" in context_text and action_intent
        if wants_meeting_actions and not reference_task_applied:
            recent=dashboard_service.list_meetings(email)
            if recent:
                if "get_recent_meetings" not in used:used.append("get_recent_meetings")
                extracted=await extract_meeting_actions(email,recent[0].id)
                for task in extracted.tasks:
                    proposals.append({"type":"task","payload":{"title":task.get("title") or "회의 후속 업무","assignee":task.get("assignee") or "담당자 미정","due":task.get("due"),"priority":task.get("priority") or "NORMAL"}})
                for schedule in extracted.schedules:
                    if schedule.get("title") and schedule.get("date") and schedule.get("time"):
                        proposals.append({"type":"schedule","payload":{"title":schedule["title"],"date":schedule["date"],"time":schedule["time"],"description":schedule.get("description")}})
                used.append("extract_meeting_actions")
                answer=f"최근 회의록에서 할 일 {len(extracted.tasks)}건과 일정 {len(extracted.schedules)}건을 찾았습니다. 아래 항목을 확인한 뒤 추가해 주세요."
                wants_calendar="캘린더" in message or "일정" in message
                has_schedule=any(item["type"]=="schedule" for item in proposals)
                if wants_calendar and not has_schedule:
                    meeting_at=datetime.fromisoformat(recent[0].meetingAt)
                    proposals.append({"type":"schedule","payload":{"title":recent[0].title,"date":meeting_at.date().isoformat(),"time":meeting_at.strftime("%H:%M"),"description":recent[0].summary}})
                    answer="최근 회의 자체를 캘린더 일정으로 준비했습니다. 아래 날짜와 시간을 확인한 뒤 추가해 주세요."
        elif "get_recent_meetings" in used:
            recent=dashboard_service.list_meetings(email)
            if recent:
                meeting=recent[0]
                participant_text=meeting.participants or "참석자 정보 없음"
                task_text=f"{len(meeting.taskIds)}건" if meeting.taskIds else "아직 없음"
                answer=(
                    f"최근 회의를 정리해 드릴게요.\n\n"
                    f"📌 {meeting.title}\n"
                    f"🗓️ {meeting.date}  ·  👥 {participant_text}\n\n"
                    f"핵심 내용\n{meeting.summary}\n\n"
                    f"연결된 후속 업무: {task_text}"
                )
            else:
                answer="아직 등록된 회의록이 없습니다. 회의록을 추가하면 핵심 내용을 정리해 드릴게요."
        if not answer:answer="요청을 처리했지만 답변을 생성하지 못했습니다."
        dashboard_service.save_agent_log(email,message,answer,used,model,latency=round((time.perf_counter()-started)*1000))
        return AgentChatResponse(answer=answer,usedTools=used,proposedActions=proposals)
    except (httpx.HTTPError,ValueError,json.JSONDecodeError) as exc:
        try:dashboard_service.save_agent_log(email,message,"",used,model,status="FAILED",error=str(exc),latency=round((time.perf_counter()-started)*1000))
        except Exception:pass
        raise HTTPException(status_code=503,detail=f"AI 업무 비서 연결 실패: {exc}") from exc

async def extract_meeting_actions(email:str,meeting_id:str)->MeetingExtractionResponse:
    meeting=next((item for item in dashboard_service.list_meetings(email) if item.id==meeting_id),None)
    if not meeting:raise HTTPException(status_code=404,detail="회의록을 찾을 수 없습니다.")
    prompt=f"""다음 회의록에서 명확하게 언급된 업무와 다음 일정을 추출하세요.
추측하지 말고 반드시 JSON 객체만 출력하세요.
형식:
{{"tasks":[{{"title":"업무","assignee":"담당자","due":"YYYY-MM-DD","priority":"NORMAL"}}],"schedules":[{{"title":"일정","date":"YYYY-MM-DD","time":"HH:MM","description":"설명"}}]}}
회의 제목: {meeting.title}
회의 일자: {meeting.date}
회의 내용: {meeting.content or meeting.summary}"""
    try:
        async with httpx.AsyncClient(base_url=settings.OLLAMA_BASE_URL,timeout=90) as client:
            response=await client.post("/api/chat",json={"model":settings.DASHBOARD_AGENT_MODEL,"messages":[{"role":"system","content":"당신은 회의록 업무·일정 추출기입니다. JSON만 출력하세요."},{"role":"user","content":prompt}],"format":"json","stream":False})
            response.raise_for_status();raw=response.json().get("message",{}).get("content","{}");data=json.loads(raw)
        return MeetingExtractionResponse(tasks=data.get("tasks") or [],schedules=data.get("schedules") or [])
    except (httpx.HTTPError,json.JSONDecodeError) as exc:
        raise HTTPException(status_code=503,detail=f"회의록 AI 분석 실패: {exc}") from exc
