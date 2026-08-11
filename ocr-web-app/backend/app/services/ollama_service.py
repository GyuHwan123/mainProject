import httpx

from app.core.config import settings


class OllamaService:
    async def health(self) -> bool:
        async with httpx.AsyncClient(base_url=settings.OLLAMA_BASE_URL) as client:
            response = await client.get("/api/tags")
            return response.is_success
