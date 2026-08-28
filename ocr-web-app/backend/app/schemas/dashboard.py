from typing import Literal
from pydantic import BaseModel, Field

TaskStatus=Literal["TODO","IN_PROGRESS","DONE"]
MeetingStatus=Literal["DRAFT","CONFIRMED","ARCHIVED"]

class ScheduleCreate(BaseModel):
    title:str=Field(min_length=1,max_length=160)
    date:str
    time:str
    description:str|None=None
    end_time:str|None=None
    meetingId:str|None=None
class ScheduleUpdate(BaseModel):
    title:str|None=Field(default=None,min_length=1,max_length=160)
    date:str|None=None
    time:str|None=None
    description:str|None=None
    end_time:str|None=None
    status:Literal["SCHEDULED","COMPLETED","CANCELED"]|None=None
class Schedule(ScheduleCreate):
    id:str
    tone:str="blue"

class TaskCreate(BaseModel):
    title:str=Field(min_length=1,max_length=200)
    assignee:str=Field(min_length=1,max_length=100)
    assigneeId:str|None=None
    due:str|None=None
    status:TaskStatus="TODO"
    priority:Literal["LOW","NORMAL","HIGH"]="NORMAL"
    description:str|None=None
    meetingId:str|None=None
class TaskUpdate(BaseModel):
    title:str|None=Field(default=None,min_length=1,max_length=200)
    assignee:str|None=Field(default=None,min_length=1,max_length=100)
    assigneeId:str|None=None
    due:str|None=None
    status:TaskStatus|None=None
    priority:Literal["LOW","NORMAL","HIGH"]|None=None
    description:str|None=None
    progress:int|None=Field(default=None,ge=0,le=100)
class Task(TaskCreate):
    id:str
    progress:int=Field(default=0,ge=0,le=100)
    urgent:bool=False

class MeetingCreate(BaseModel):
    title:str=Field(min_length=1,max_length=160)
    meetingAt:str
    content:str|None=None
    summary:str|None=None
    participants:list[str]=Field(default_factory=list)
    status:MeetingStatus="DRAFT"
class MeetingUpdate(BaseModel):
    title:str|None=Field(default=None,min_length=1,max_length=160)
    meetingAt:str|None=None
    content:str|None=None
    summary:str|None=None
    participants:list[str]|None=None
    status:MeetingStatus|None=None
class Meeting(BaseModel):
    id:str
    date:str
    title:str
    participants:str
    summary:str
    tag:str
    taskIds:list[str]
    content:str|None=None
    status:MeetingStatus="DRAFT"
    meetingAt:str

class Briefing(BaseModel):
    date:str
    summary:str
    events:list[Schedule]
    urgent_tasks:list[Task]
    meeting_decisions:list[str]

class AgentChatRequest(BaseModel):
    message:str=Field(min_length=1,max_length=1000)
    history:list[dict]=Field(default_factory=list)
class AgentChatResponse(BaseModel):
    answer:str
    usedTools:list[str]
    proposedActions:list[dict]=Field(default_factory=list)

class MeetingExtractionRequest(BaseModel):
    meeting_id:str
class MeetingExtractionResponse(BaseModel):
    tasks:list[dict]=Field(default_factory=list)
    schedules:list[dict]=Field(default_factory=list)
