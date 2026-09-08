import logging
from time import perf_counter
from typing import Any

import httpx
from fastapi import HTTPException

from app.core.config import settings
from app.services.rag_service import can_access_company_rag
from app.services.supabase_service import supabase_service


SUMMARY_BATCH_CHARS = 6_000
SUMMARY_MAX_PREDICT = 650
logger = logging.getLogger("uvicorn.error")


def _group_texts(texts: list[str], max_chars: int = SUMMARY_BATCH_CHARS) -> list[str]:
    groups: list[str] = []
    current: list[str] = []
    current_length = 0
    for text in (value.strip() for value in texts if value and value.strip()):
        parts = [text[index:index + max_chars] for index in range(0, len(text), max_chars)]
        for part in parts:
            separator_length = 2 if current else 0
            if current and current_length + separator_length + len(part) > max_chars:
                groups.append("\n\n".join(current))
                current = []
                current_length = 0
            current.append(part)
            current_length += separator_length + len(part)
    if current:
        groups.append("\n\n".join(current))
    return groups


async def _generate_summary(prompt: str) -> str:
    payload = {
        "model": settings.RAG_LLM_MODEL,
        "prompt": prompt,
        "stream": False,
        "keep_alive": "30m",
        "options": {
            "temperature": 0.05,
            "num_predict": SUMMARY_MAX_PREDICT,
            "num_ctx": 8192,
            "repeat_penalty": 1.08,
        },
    }
    started_at = perf_counter()
    empty_response = False
    try:
        async with httpx.AsyncClient(base_url=settings.OLLAMA_BASE_URL.rstrip("/"), timeout=180) as client:
            response = await client.post("/api/generate", json=payload)
            response.raise_for_status()
            summary = str(response.json().get("response") or "").strip()
        if not summary:
            empty_response = True
            raise ValueError("empty summary response")
        return summary
    except (httpx.HTTPError, ValueError) as exc:
        if isinstance(exc, httpx.TimeoutException):
            reason = "timeout"
        elif isinstance(exc, httpx.ConnectError):
            reason = "connect_error"
        elif isinstance(exc, httpx.HTTPStatusError):
            reason = "http_status"
        elif empty_response:
            reason = "empty_response"
        elif isinstance(exc, ValueError):
            reason = "invalid_response"
        else:
            reason = "http_error"
        logger.error(
            "Ollama document summary failed reason=%s exception_type=%s "
            "model=%s endpoint=/api/generate elapsed_seconds=%.2f "
            "timeout_seconds=180 status_code=%s",
            reason, type(exc).__name__, settings.RAG_LLM_MODEL,
            perf_counter() - started_at,
            exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None,
        )
        raise HTTPException(status_code=503, detail="문서 요약에 실패했습니다.") from exc


def _partial_prompt(content: str) -> str:
    return f"""다음은 한 문서의 일부입니다. 최종 통합 단계에서 사용할 중립적인 중간 요약을 작성하세요.

작성 원칙:
- 해당 부분의 주제와 핵심 내용을 중심으로 주요 사실, 수치, 조건, 관계, 결론을 가능한 한 정확하게 보존하세요.
- 문서의 목적과 흐름을 고려하되, 원문에 실제로 나타난 정보만 요약하세요.
- 중요한 수치와 단위, 날짜, 고유명사, 비교 관계, 예외 조건은 의미가 달라지지 않도록 원문에 가깝게 유지하세요.
- 문서에 특정한 절차, 원인, 해결 방법, 성과, 모델, 기술, 사례, 제한 사항 등이 포함되어 있다면 관련된 범위에서 보존하세요. 문서에 없는 항목을 추측하거나 채우지 마세요.
- 같은 내용의 반복은 압축할 수 있지만, 이 부분에서 처음 등장하는 새로운 사실, 조건, 수치, 결론은 삭제하지 마세요.
- 최종 단계가 중요도를 다시 판단할 수 있도록 충분한 세부 정보를 남기고 지나치게 짧게 압축하지 마세요.
- 문서에 없는 내용을 추측하거나 일반 지식으로 추가하지 마세요.
- 작성 지시나 "중간 요약"이라는 표현을 결과에 언급하지 마세요.
- 별표, 번호 목록, 제목, 굵게 표시 등 Markdown 문법을 전혀 사용하지 말고 자연스러운 한국어 plain text 문단으로만 작성하세요.

[문서 일부]
{content}

[부분 요약]"""


def _final_prompt(content: str) -> str:
    return f"""다음 내용은 하나의 문서에서 원문 순서대로 만든 중립적인 중간 요약들입니다. 모든 부분 요약을 끝까지 검토한 뒤, 각 부분의 고유 핵심 사실을 문서 전체 흐름으로 통합하세요.

작성 원칙:
- 앞부분 요약이 더 길거나 같은 내용을 반복한다는 이유로 후반 부분의 고유 정보를 버리지 마세요.
- 각 부분에서 처음 등장한 새로운 핵심 내용, 주요 사실, 수치, 조건, 중요한 관계와 결론을 우선 보존하세요.
- 문서의 주제와 목적에 맞는 흐름을 유지하되, 특정한 문제 해결이나 실험 보고서 형식으로 재구성하지 마세요.
- 부분 요약에 포함된 중요한 수치, 날짜, 고유명사, 비교 관계와 예외 조건은 최종 결과에서 삭제하거나 일반화하지 마세요.
- 문서에 명시된 절차, 원인, 해결 방법, 성과, 모델, 기술, 사례 또는 제한 사항만 해당 문서의 맥락에 맞게 포함하세요.
- 같은 사실의 반복은 통합하되, 뒤쪽 부분에서 새로 등장한 사실과 결론은 반드시 포함하세요.
- "간결하게"를 지나치게 적용해 핵심 내용을 삭제하지 마세요. 문서의 주제와 결론을 이해할 수 있는 충분한 길이의 3~5개 문단으로 작성하세요.
- 문서에 없는 내용을 추측하거나 생성하지 마세요.
- 출력 전에 모든 부분 요약을 다시 확인하여 각 부분에서 최소 하나 이상의 고유 사실이 최종 결과에 반영됐는지, 후반부의 새 핵심 내용과 중요한 결론이 포함됐는지 내부적으로 검수하세요. 검수 과정은 출력하지 마세요.
- 작성 지시, 부분 요약, 중간 요약이라는 표현을 결과에 언급하지 마세요.
- 별표, 번호 목록, 제목, 굵게 표시 등 Markdown 문법을 전혀 사용하지 말고 3~5개의 자연스러운 한국어 plain text 문단으로만 작성하세요.

[부분 요약]
{content}

[최종 문서 요약]"""


async def _summarize_chunks(chunks: list[dict[str, Any]]) -> str:
    groups = _group_texts([str(chunk.get("content") or "") for chunk in chunks])
    if not groups:
        raise HTTPException(status_code=422, detail="요약할 문서 내용이 없습니다.")
    if len(groups) == 1:
        return await _generate_summary(_final_prompt(groups[0]))
    summaries = [await _generate_summary(_partial_prompt(group)) for group in groups]
    while len(summaries) > 1:
        summary_groups = _group_texts(summaries, max_chars=6_000)
        summaries = [await _generate_summary(_final_prompt(group)) for group in summary_groups]
    return summaries[0]


async def get_or_create_document_summary(
    user_email: str, rag_document_id: str, *, user_role: str, subscription_tier: str,
    force_regenerate: bool = False,
) -> dict[str, Any]:
    document = supabase_service.get_accessible_rag_document(
        user_email, rag_document_id,
        include_company_documents=can_access_company_rag(user_role, subscription_tier),
    )
    cached_summary = str(document.get("summary") or "").strip()
    if cached_summary and not force_regenerate:
        return {"document_id": rag_document_id, "summary": cached_summary, "cached": True}

    chunks = supabase_service.list_all_rag_chunks(rag_document_id)
    summary = await _summarize_chunks(chunks)
    supabase_service.save_rag_document_summary(rag_document_id, summary)
    return {"document_id": rag_document_id, "summary": summary, "cached": False}
