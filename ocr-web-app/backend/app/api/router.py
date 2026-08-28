from fastapi import APIRouter

from app.api.routes import agent, auth, chatbot, dashboard, finance, finance_evaluations, ocr, rag, rag_evaluations, reports, users

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(ocr.router, prefix="/ocr", tags=["ocr"])
api_router.include_router(chatbot.router, prefix="/chatbot", tags=["chatbot"])
api_router.include_router(rag.router, prefix="/rag", tags=["rag"])
api_router.include_router(rag_evaluations.router, prefix="/rag", tags=["rag-evaluations"])
api_router.include_router(reports.router, prefix="/reports", tags=["reports"])
api_router.include_router(finance.router, prefix="/finance", tags=["finance"])
api_router.include_router(finance_evaluations.router, prefix="/finance-evaluations", tags=["finance-evaluations"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(dashboard.router, prefix="/dashboard", tags=["dashboard"])
api_router.include_router(agent.router, prefix="/agent", tags=["agent"])
