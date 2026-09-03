from __future__ import annotations

import argparse
import hashlib
import json
import os
import pickle
from pathlib import Path
from typing import Any

import httpx
import numpy as np

DOC_IDS = (
    "HR-001", "HR-002", "HR-003", "HR-004", "HR-005",
    "GA-001", "GA-002", "GA-003", "GA-004", "IS-001", "IS-002",
    "SH-001", "SH-002", "SH-003", "SH-004", "ER-001", "ER-002", "ER-003",
)
EXPECTED_CHUNKS = 37
EXPECTED_DIMENSIONS = 1024
EXPECTED_OTHER_CHUNKS = 245
EXPECTED_FINGERPRINT = "07fda11679ca57f21fbe364386068538b824d563c7096ce5bd72913f1543dcce"
TARGET_ROW = (
    "[표 행] 항목: 숙박비 | 일반직원: 1박 150,000원 한도 | "
    "팀장 이상: 1박 180,000원 한도 | 비고: 세금·봉사료 포함"
)
FLAT_TABLE = "항목\n일반직원\n팀장 이상\n비고\n교통비\n실비\n실비"


def request_headers(key: str, prefer: str | None = None) -> dict[str, str]:
    result = {"apikey": key, "Content-Type": "application/json"}
    if key.startswith("eyJ"):
        result["Authorization"] = f"Bearer {key}"
    if prefer:
        result["Prefer"] = prefer
    return result


def ensure_ok(response: httpx.Response, operation: str) -> None:
    if response.status_code >= 400:
        raise RuntimeError(f"{operation} 실패 ({response.status_code}): {response.text}")


def get_all(client: httpx.Client, url: str, headers: dict[str, str], params: dict[str, str]) -> list[dict]:
    rows: list[dict] = []
    while True:
        response = client.get(url, headers=headers, params={**params, "limit": "1000", "offset": str(len(rows))})
        ensure_ok(response, "조회")
        page = response.json()
        rows.extend(page)
        if len(page) < 1000:
            return rows


def stable_hash(rows: list[dict]) -> str:
    value = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def parse_vector(value: Any) -> np.ndarray:
    return np.fromstring(value.strip("[]"), sep=",", dtype=np.float32) if isinstance(value, str) else np.asarray(value, dtype=np.float32)


def load_inputs() -> tuple[list[dict], np.ndarray, dict]:
    with Path("/input/company_chunks.pkl").open("rb") as file:
        chunks = pickle.load(file)
    with Path("/output/baseline_embeddings.pkl").open("rb") as file:
        embeddings = np.asarray(pickle.load(file))
    metadata = json.loads(Path("/output/baseline_metadata.json").read_text(encoding="utf-8"))
    if len(chunks) != EXPECTED_CHUNKS or len({chunk["doc_id"] for chunk in chunks}) != 18:
        raise ValueError("확정 18개/37개 chunk 입력이 아닙니다.")
    if embeddings.shape != (EXPECTED_CHUNKS, EXPECTED_DIMENSIONS) or not np.isfinite(embeddings).all():
        raise ValueError(f"Baseline embedding이 유효하지 않습니다: {embeddings.shape}")
    if metadata.get("label") != "baseline" or metadata.get("embedding_model") != "BAAI/bge-m3":
        raise ValueError("Baseline metadata 모델/label이 일치하지 않습니다.")
    if metadata.get("chunk_fingerprint_sha256") != EXPECTED_FINGERPRINT:
        raise ValueError("Baseline chunk fingerprint가 일치하지 않습니다.")
    return chunks, embeddings, metadata


def context(client: httpx.Client, base: str, headers: dict[str, str]) -> dict:
    documents = get_all(client, base + "/rag_documents", headers, {
        "select": "*", "doc_id": f"in.({','.join(DOC_IDS)})", "order": "doc_id.asc",
    })
    if len(documents) != 18 or {row["doc_id"] for row in documents} != set(DOC_IDS):
        raise RuntimeError("DB 기업문서가 지정한 18개와 일치하지 않습니다.")
    document_ids = {row["doc_id"]: row["id"] for row in documents}
    other_documents = get_all(client, base + "/rag_documents", headers, {
        "select": "id", "doc_id": f"not.in.({','.join(DOC_IDS)})", "order": "id.asc",
    })
    other_ids = [row["id"] for row in other_documents]
    other_chunks = get_all(client, base + "/rag_chunks", headers, {
        "select": "*", "document_id": f"in.({','.join(other_ids)})", "order": "document_id.asc,chunk_index.asc",
    }) if other_ids else []
    if len(other_chunks) != EXPECTED_OTHER_CHUNKS:
        raise RuntimeError(f"일반 RAG chunk가 {EXPECTED_OTHER_CHUNKS}개가 아닙니다: {len(other_chunks)}")
    return {
        "documents": documents,
        "document_ids": document_ids,
        "documents_hash": stable_hash(documents),
        "other_ids": other_ids,
        "other_chunks": other_chunks,
        "other_hash": stable_hash(other_chunks),
    }


def company_rows(client: httpx.Client, base: str, headers: dict[str, str], ids: list[str]) -> list[dict]:
    return get_all(client, base + "/rag_chunks", headers, {
        "select": "document_id,chunk_index,page_number,content,document_title,section_title,section_path,heading_level,bbox,embedding",
        "document_id": f"in.({','.join(ids)})", "order": "document_id.asc,chunk_index.asc",
    })


def main() -> None:
    parser = argparse.ArgumentParser(description="Baseline 기업 RAG chunk를 Supabase REST로 upsert-first 동기화합니다.")
    parser.add_argument("--apply", action="store_true", help="실제 upsert와 stale cleanup 수행")
    args = parser.parse_args()
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not url or not key:
        raise RuntimeError("SUPABASE_URL과 SUPABASE_SERVICE_ROLE_KEY가 필요합니다.")
    chunks, embeddings, metadata = load_inputs()
    base, headers = url.rstrip("/") + "/rest/v1", request_headers(key)
    with httpx.Client(timeout=120) as client:
        state = context(client, base, headers)
        ids = list(state["document_ids"].values())
        before = company_rows(client, base, headers, ids)
        preflight = {"company_documents": 18, "company_chunks_before": len(before), "other_chunks_before": len(state["other_chunks"]), **metadata}
        if not args.apply:
            print(json.dumps(preflight, ensure_ascii=False, indent=2))
            return

        payload = [{
            "document_id": state["document_ids"][chunk["doc_id"]],
            "chunk_index": int(chunk["chunk_index"]), "page_number": int(chunk["page"]),
            "content": chunk["text"], "document_title": chunk["title"],
            "section_title": None, "section_path": [], "heading_level": None,
            "bbox": None, "embedding": embedding.tolist(),
        } for chunk, embedding in zip(chunks, embeddings)]
        response = client.post(
            base + "/rag_chunks", params={"on_conflict": "document_id,chunk_index"},
            headers=request_headers(key, "resolution=merge-duplicates,return=representation"), json=payload,
        )
        ensure_ok(response, "37개 bulk upsert")
        if len(response.json()) != EXPECTED_CHUNKS:
            raise RuntimeError("upsert 반환이 37개가 아니므로 stale cleanup을 중단합니다.")

        after_upsert = company_rows(client, base, headers, ids)
        indexed = {(row["document_id"], int(row["chunk_index"])): row for row in after_upsert}
        for expected, expected_embedding in zip(payload, embeddings):
            row = indexed.get((expected["document_id"], expected["chunk_index"]))
            if not row or row["content"] != expected["content"] or int(row["page_number"]) != expected["page_number"]:
                raise RuntimeError("upsert content/index 검증 실패; stale cleanup을 중단합니다.")
            actual_embedding = parse_vector(row["embedding"])
            if actual_embedding.shape != (EXPECTED_DIMENSIONS,) or not np.allclose(actual_embedding, expected_embedding, atol=1e-6):
                raise RuntimeError("upsert embedding 검증 실패; stale cleanup을 중단합니다.")

        counts = {doc_id: sum(chunk["doc_id"] == doc_id for chunk in chunks) for doc_id in DOC_IDS}
        for doc_id in DOC_IDS:
            response = client.delete(base + "/rag_chunks", headers=headers, params={
                "document_id": f"eq.{state['document_ids'][doc_id]}",
                "chunk_index": f"gte.{counts[doc_id]}",
            })
            ensure_ok(response, f"{doc_id} stale cleanup")

        final_company = company_rows(client, base, headers, ids)
        final_state = context(client, base, headers)
        final_documents_hash = stable_hash(final_state["documents"])
        dimensions = [parse_vector(row["embedding"]).shape for row in final_company]
        result = {
            "company_documents": len(final_state["documents"]),
            "company_chunks": len(final_company),
            "other_chunks": len(final_state["other_chunks"]),
            "embedding_1024_count": sum(shape == (EXPECTED_DIMENSIONS,) for shape in dimensions),
            "table_chunk_count": sum("[표 행]" in row["content"] for row in final_company),
            "lodging_row_count": sum(TARGET_ROW in row["content"] for row in final_company),
            "flattened_lodging_count": sum(FLAT_TABLE in row["content"] for row in final_company),
            "rag_documents_unchanged": final_documents_hash == state["documents_hash"],
            "other_chunks_unchanged": stable_hash(final_state["other_chunks"]) == state["other_hash"],
            **metadata,
        }
        valid = (
            result["company_documents"] == 18 and result["company_chunks"] == EXPECTED_CHUNKS
            and result["other_chunks"] == EXPECTED_OTHER_CHUNKS
            and result["embedding_1024_count"] == EXPECTED_CHUNKS
            and result["table_chunk_count"] > 0 and result["lodging_row_count"] == 1
            and result["flattened_lodging_count"] == 0
            and result["rag_documents_unchanged"] and result["other_chunks_unchanged"]
        )
        if not valid:
            raise RuntimeError("최종 검증 실패; 평가 실행 금지: " + json.dumps(result, ensure_ascii=False))
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
