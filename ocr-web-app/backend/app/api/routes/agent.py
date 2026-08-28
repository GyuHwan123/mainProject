from fastapi import APIRouter,Depends
from app.api.routes.auth import require_current_user
from app.models.user import User
from app.schemas.dashboard import AgentChatRequest,AgentChatResponse,MeetingExtractionRequest,MeetingExtractionResponse
from app.services.agent_service import chat,extract_meeting_actions
router=APIRouter()
@router.post("/chat",response_model=AgentChatResponse)
async def agent_chat(payload:AgentChatRequest,user:User=Depends(require_current_user))->AgentChatResponse:return await chat(user.email,payload.message,payload.history)
@router.post("/extract-actions",response_model=MeetingExtractionResponse)
async def extract_actions(payload:MeetingExtractionRequest,user:User=Depends(require_current_user))->MeetingExtractionResponse:return await extract_meeting_actions(user.email,payload.meeting_id)
