from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = PROJECT_ROOT / "backend"


class Settings(BaseSettings):
    APP_NAME: str = "OCR Web App"
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 720
    # Local development uses the native Ollama service. Docker Compose overrides
    # this with http://ollama:11434 for container-to-container communication.
    OLLAMA_BASE_URL: str = "http://127.0.0.1:11434"
    RAG_EMBEDDING_MODEL: str = "BAAI/bge-m3"
    RAG_EMBEDDING_DIMENSIONS: int = 1024
    RAG_LLM_MODEL: str = "gemma2:2b"
    DASHBOARD_AGENT_MODEL: str = "gemma2:2b"
    RAG_RERANK_MODEL: str = "BAAI/bge-reranker-v2-m3"
    RECEIPTS_LLM_MODEL: str = ""
    RECEIPTS_LLM_KEEP_ALIVE: str = "0s"
    RECEIPTS_LLM_NUM_CTX: int = 4096
    RECEIPTS_LLM_TIMEOUT_SECONDS: int = 600
    RECEIPTS_CLASSIFICATION_BUDGET_SECONDS: int = 630
    RAG_PROMPT_VERSION: str = "baseline-v1"
    RAG_TOP_K: int = 4
    RAG_DENSE_CANDIDATE_COUNT: int = 12
    RAG_BM25_CANDIDATE_COUNT: int = 12
    RAG_QUERY_REWRITING: bool = False
    RAG_QUERY_REWRITE_MODEL: str = ""
    RAG_QUERY_REWRITE_TIMEOUT_SECONDS: float = 45.0
    RAG_ANSWERABILITY_THRESHOLD: float = 0.01
    RAG_CHUNK_TARGET_CHARS: int = 380
    RAG_TEXT_CHUNK_MAX_CHARS: int = 900
    RAG_TEXT_CHUNK_OVERLAP_CHARS: int = 120
    RAG_EVALUATION_ANSWER_THRESHOLD: float = 0.75
    # Docker Compose overrides this with the internal `ocr` service address.
    OCR_BASE_URL: str = "http://127.0.0.1:8001"
    CORS_ORIGINS: list[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]
    SUPABASE_URL: str = ""
    SUPABASE_ANON_KEY: str = ""
    SUPABASE_SERVICE_ROLE_KEY: str = ""
    SUPABASE_USERS_TABLE: str = "users"
    SUPABASE_OCR_DOCUMENTS_TABLE: str = "ocr_documents"
    SUPABASE_DOCUMENTS_BUCKET: str = "documents"
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM_EMAIL: str = ""
    SMTP_FROM_NAME: str = "DocAI"
    FRONTEND_URL: str = ""
    TOSS_SECRET_KEY: str = ""
    model_config = SettingsConfigDict(
        env_file=(PROJECT_ROOT / ".env", BACKEND_ROOT / ".env"),
        extra="ignore",
    )

    @field_validator("SECRET_KEY")
    @classmethod
    def validate_secret_key(cls, value: str) -> str:
        secret = value.strip()
        forbidden = {
            "change-me", "changeme", "secret", "development", "dev-secret",
            "replace-with-a-long-random-secret", "your-secret-key",
        }
        if secret.lower() in forbidden:
            raise ValueError("SECRET_KEY에 기본값이나 예제 값을 사용할 수 없습니다.")
        if len(secret) < 48:
            raise ValueError("SECRET_KEY는 최소 48자 이상의 난수여야 합니다.")
        return secret

    @field_validator("ALGORITHM")
    @classmethod
    def validate_algorithm(cls, value: str) -> str:
        if value != "HS256":
            raise ValueError("ALGORITHM은 HS256만 사용할 수 있습니다.")
        return value

    @field_validator("ACCESS_TOKEN_EXPIRE_MINUTES")
    @classmethod
    def validate_access_token_expiry(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("ACCESS_TOKEN_EXPIRE_MINUTES는 1 이상이어야 합니다.")
        return value

    @field_validator("CORS_ORIGINS")
    @classmethod
    def validate_cors_origins(cls, value: list[str]) -> list[str]:
        origins = [origin.rstrip("/") for origin in value if origin.strip()]
        if not origins or "*" in origins:
            raise ValueError("CORS_ORIGINS에는 허용할 Origin을 명시적으로 지정해야 합니다.")
        return origins


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
