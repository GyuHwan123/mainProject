from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import settings

@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.services.rag_service import _get_embedding_model

    try:
        _get_embedding_model()
    except Exception as exc:
        raise RuntimeError(
            f"RAG embedding model failed to load: {settings.RAG_EMBEDDING_MODEL}. "
            "Base model fallback is disabled."
        ) from exc
    yield


app = FastAPI(title=settings.APP_NAME, version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api/v1")


@app.get("/health", tags=["health"])
def health_check() -> dict[str, str]:
    return {"status": "ok"}
