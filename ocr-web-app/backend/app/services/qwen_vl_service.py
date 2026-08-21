import base64
import json
import re
from typing import Any

import httpx

from app.core.config import settings


RECEIPT_PROMPT = """당신은 한국어 영수증 이미지를 분석하여 재무 문서 작성에 필요한 정보를 추출하는 AI입니다.
이미지에서 확인 가능한 사실만 사용하고, 확인할 수 없는 값은 추측하지 말고 null로 반환하세요.
출력은 반드시 유효한 JSON 하나만 반환하고 설명문은 쓰지 마세요.
key 이름을 바꾸지 마세요.

출력 스키마:
{
  "가게명": null,
  "구매일자": null,
  "구매물품": [{"상품명": null, "단가": null, "수량": null, "금액": null}],
  "총 물품 수량": null,
  "총 결제액": null,
  "카테고리": null,
  "결제방식": null,
  "카드번호": null
}

이 이미지를 분석하세요."""


def is_configured() -> bool:
    return bool(settings.QWEN_VL_API_URL.strip())


def model_name() -> str:
    return settings.QWEN_VL_MODEL_NAME.strip() or "qwen2-vl-receipt-finetuned"


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        for key in ("result", "prediction", "output"):
            if key in value:
                return _json_object(value[key])
        return value
    if not isinstance(value, str):
        raise ValueError("Qwen-VL response must contain a JSON object")
    text = value.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise ValueError("Qwen-VL returned invalid JSON")
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("Qwen-VL response JSON must be an object")
    return parsed


def to_finance_schema(result: dict[str, Any]) -> dict[str, Any]:
    items = result.get("구매물품") if isinstance(result.get("구매물품"), list) else result.get("items")
    converted_items = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        converted_items.append({
            "name": item.get("상품명", item.get("name")),
            "quantity": item.get("수량", item.get("quantity")),
            "unit_price": item.get("단가", item.get("unit_price")),
            "total_amount": item.get("금액", item.get("total_amount")),
        })
    return {
        **result,
        "merchant": result.get("가게명", result.get("merchant")),
        "transaction_date": result.get("구매일자", result.get("transaction_date")),
        "expense_category": result.get("카테고리", result.get("expense_category")),
        "total_amount": result.get("총 결제액", result.get("total_amount")),
        "payment_method": result.get("결제방식", result.get("payment_method")),
        "items": converted_items,
        "_model_name": model_name(),
    }


async def generate_with_image(content: bytes, mime_type: str, filename: str, prompt: str) -> Any:
    if not is_configured():
        raise RuntimeError("QWEN_VL_API_URL is not configured")
    headers = {"Content-Type": "application/json"}
    if settings.QWEN_VL_API_TOKEN:
        headers["Authorization"] = f"Bearer {settings.QWEN_VL_API_TOKEN}"
    payload = {
        "image_base64": base64.b64encode(content).decode("ascii"),
        "mime_type": mime_type,
        "filename": filename,
        "prompt": prompt,
    }
    async with httpx.AsyncClient(timeout=300) as client:
        response = await client.post(settings.QWEN_VL_API_URL, headers=headers, json=payload)
        response.raise_for_status()
    payload = response.json()
    if isinstance(payload, dict):
        return payload.get("result", payload.get("output", payload.get("prediction", payload)))
    return payload


async def predict_receipt(content: bytes, mime_type: str, filename: str) -> dict[str, Any]:
    output = await generate_with_image(content, mime_type, filename, RECEIPT_PROMPT)
    return to_finance_schema(_json_object(output))
