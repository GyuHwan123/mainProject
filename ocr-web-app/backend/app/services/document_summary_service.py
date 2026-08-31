from typing import Any

import httpx
from fastapi import HTTPException

from app.core.config import settings
from app.services.rag_service import can_access_company_rag
from app.services.supabase_service import supabase_service


SUMMARY_BATCH_CHARS = 3_500
SUMMARY_MAX_PREDICT = 650


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
    try:
        async with httpx.AsyncClient(base_url=settings.OLLAMA_BASE_URL.rstrip("/"), timeout=180) as client:
            response = await client.post("/api/generate", json=payload)
            response.raise_for_status()
            summary = str(response.json().get("response") or "").strip()
        if not summary:
            raise ValueError("empty summary response")
        return summary
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="문서 요약에 실패했습니다.") from exc


def _partial_prompt(content: str) -> str:
    return f"""다음은 한 문서의 일부입니다. 최종 통합 단계에서 사용할 핵심 사실 보존용 중간 요약을 작성하세요.

작성 원칙:
- 해당 부분에서 새롭게 등장한 핵심 사실을 가능한 한 보존하세요.
- 문제와 원인, 적용한 해결 방법, 변경 사항, 실험·검증 결과, 개선 전후 수치, latency·accuracy 등의 성능 지표, 모델명·기술명, 실패 사례, 남은 문제, 향후 과제가 있으면 생략하지 마세요.
- 비교 수치는 전후 관계가 드러나게 원문 수치와 단위를 그대로 유지하세요. 예: "기존 6.90초 → 개선 후 1.61ms". 이를 단순히 "성능이 개선됐다"로 치환하지 마세요.
- 성능·실험·검증과 관련된 수치와 단위, 판정 변화 건수, 모델명, 기술명, 질문 ID는 원문 표기를 바꾸거나 뭉뚱그리지 말고 모두 남기세요.
- 정상 통과, 정상 거절, False Rejection, False Acceptance처럼 서로 반대되는 판정 결과를 혼동하거나 "정답 제공" 같은 다른 의미로 바꾸지 마세요.
- 테스트 질문이나 실행 예시에 등장한 내용을 문서 자체의 주제·정책·규정으로 오인하지 마세요. 예를 들어 재택근무 질문이 False Acceptance 테스트 사례라면 문서가 재택근무 규정을 설명한다고 요약하면 안 됩니다.
- UI/OCR 출력, 검색 결과, 테스트 질문은 화면 문구를 나열하기보다 왜 그 사례가 제시됐는지와 무엇을 검증하는지를 중심으로 해석하세요.
- 같은 내용의 반복은 압축할 수 있지만, 이 부분에서 처음 등장하는 새로운 결과·수치·결론은 삭제하지 마세요.
- 최종 단계가 중요도를 다시 판단할 수 있도록 충분한 세부 정보를 남기고 지나치게 짧게 압축하지 마세요.
- 문서에 없는 내용을 추측하거나 일반 지식으로 추가하지 마세요.
- 작성 지시나 "중간 요약"이라는 표현을 결과에 언급하지 마세요.
- 별표, 번호 목록, 제목, 굵게 표시 등 Markdown 문법을 전혀 사용하지 말고 자연스러운 한국어 plain text 문단으로만 작성하세요.

[문서 일부]
{content}

[부분 요약]"""


def _final_prompt(content: str) -> str:
    return f"""다음 내용은 하나의 문서에서 원문 순서대로 만든 핵심 사실 보존용 중간 요약들입니다. 모든 부분 요약을 끝까지 검토한 뒤, 각 부분의 고유 핵심 사실을 문서 전체 흐름으로 통합하세요.

작성 원칙:
- 앞부분 요약이 더 길거나 같은 내용을 반복한다는 이유로 후반 부분의 고유 정보를 버리지 마세요.
- 각 부분에서 처음 등장한 새로운 문제, 새로운 해결 방법, 새로운 성능 결과, 중요한 수치, 최종 결론, 남은 문제와 향후 과제를 우선 보존하세요.
- 문서가 "기존 문제 → 원인 분석 → 1차 개선 → 개선 결과 → 새롭게 발견된 문제 → 2차 개선 → 최종 결과 → 남은 문제"로 전개된다면 이 흐름을 유지하세요.
- 개선 전후 수치는 원문 값과 단위를 보존하고 관계가 명확히 보이도록 작성하세요.
- 부분 요약에 있는 성능·실험·검증 수치, 판정 변화 건수, 모델명, 기술명, 질문 ID는 최종 결과에서 삭제하거나 일반화하지 마세요.
- 정상 통과, 정상 거절, False Rejection, False Acceptance의 방향을 그대로 유지하고 서로 다른 결과로 바꾸지 마세요.
- 테스트 질문, UI/OCR 출력, 검색 결과 예시는 문서 자체의 주제나 정책으로 오인하지 말고, 해당 사례가 검증하는 성공·실패와 개선 효과를 설명하세요.
- 같은 사실의 반복은 통합하되, 뒤쪽 부분에서 새로 등장한 사실과 결론은 반드시 포함하세요.
- "간결하게"를 지나치게 적용해 핵심 결과를 삭제하지 마세요. 문서의 주요 단계와 최종 상태를 이해할 수 있는 충분한 길이의 3~5개 문단으로 작성하세요.
- 문서에 없는 내용을 추측하거나 생성하지 마세요.
- 출력 전에 모든 부분 요약을 다시 확인하여 각 부분에서 최소 하나 이상의 고유 사실이 최종 결과에 반영됐는지, 후반부의 새 성능 결과와 남은 문제가 포함됐는지 내부적으로 검수하세요. 검수 과정은 출력하지 마세요.
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
