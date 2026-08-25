from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = PROJECT_ROOT / "backend"


class Settings(BaseSettings):
    APP_NAME: str = "OCR Web App"
    SECRET_KEY: str = "change-me"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 720
    # Local development uses the native Ollama service. Docker Compose overrides
    # this with http://ollama:11434 for container-to-container communication.
    OLLAMA_BASE_URL: str = "http://127.0.0.1:11434"
    RAG_EMBEDDING_MODEL: str = "embeddinggemma"
    RAG_EMBEDDING_DIMENSIONS: int = 768
    RAG_LLM_MODEL: str = "gemma2:2b"
    RECEIPTS_LLM_MODEL: str = ""
    RAG_RERANK_MODEL: str = ""
    RAG_PROMPT_VERSION: str = "baseline-v1"
    RAG_TOP_K: int = 8
    RAG_CHUNK_TARGET_CHARS: int = 380
    # Docker Compose overrides this with the internal `ocr` service address.
    OCR_BASE_URL: str = "http://127.0.0.1:8001"
    CORS_ORIGINS: list[str] = ["http://localhost:3000"]
    SUPABASE_URL: str = ""
    SUPABASE_ANON_KEY: str = ""
    SUPABASE_SERVICE_ROLE_KEY: str = ""
    SUPABASE_USERS_TABLE: str = "users"
    SUPABASE_OCR_DOCUMENTS_TABLE: str = "ocr_documents"
    SUPABASE_DOCUMENTS_BUCKET: str = "documents"
    GOOGLE_CLIENT_ID: str = ""
    APPLE_CLIENT_ID: str = ""
    KAKAO_CLIENT_ID: str = ""

    model_config = SettingsConfigDict(
        env_file=(PROJECT_ROOT / ".env", BACKEND_ROOT / ".env"),
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
