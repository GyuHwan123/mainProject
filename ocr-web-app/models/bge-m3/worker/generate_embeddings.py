from __future__ import annotations

import argparse
import hashlib
import json
import os
import pickle
from pathlib import Path

import numpy as np
import torch
from sentence_transformers import SentenceTransformer

EXPECTED_DOCUMENTS = 18
EXPECTED_CHUNKS = 37
EXPECTED_DIMENSIONS = 1024
EXPECTED_TABLE_CHUNKS = 26
TARGET_TABLE_ROW = (
    "[표 행] 항목: 숙박비 | 일반직원: 1박 150,000원 한도 | "
    "팀장 이상: 1박 180,000원 한도 | 비고: 세금·봉사료 포함"
)


def chunk_fingerprint(chunks: list[dict]) -> str:
    stable = [
        {
            "doc_id": chunk["doc_id"],
            "chunk_index": int(chunk["chunk_index"]),
            "page": int(chunk["page"]),
            "text": chunk["text"],
        }
        for chunk in chunks
    ]
    payload = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_and_validate_chunks(chunks_path: Path, manifest_path: Path) -> tuple[list[dict], str]:
    with chunks_path.open("rb") as file:
        chunks = pickle.load(file)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(chunks, list) or len(chunks) != EXPECTED_CHUNKS:
        raise ValueError(f"확정 chunk는 {EXPECTED_CHUNKS}개여야 합니다.")
    if len({chunk["doc_id"] for chunk in chunks}) != EXPECTED_DOCUMENTS:
        raise ValueError(f"기업문서는 {EXPECTED_DOCUMENTS}개여야 합니다.")
    if sum("[표 행]" in chunk["text"] for chunk in chunks) != EXPECTED_TABLE_CHUNKS:
        raise ValueError(f"[표 행] 포함 chunk는 {EXPECTED_TABLE_CHUNKS}개여야 합니다.")
    if sum(TARGET_TABLE_ROW in chunk["text"] for chunk in chunks) != 1:
        raise ValueError("출장비 숙박비 구조화 행이 정확히 1개여야 합니다.")
    if manifest.get("document_count") != EXPECTED_DOCUMENTS or manifest.get("chunk_count") != EXPECTED_CHUNKS:
        raise ValueError("확정 manifest와 chunk가 일치하지 않습니다.")
    indexes: dict[str, list[int]] = {}
    for chunk in chunks:
        indexes.setdefault(chunk["doc_id"], []).append(int(chunk["chunk_index"]))
    for doc_id, values in indexes.items():
        if sorted(values) != list(range(len(values))):
            raise ValueError(f"{doc_id} chunk_index가 연속적이지 않습니다.")
    return chunks, chunk_fingerprint(chunks)


def atomic_pickle(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as file:
        pickle.dump(value, file)
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description="확정된 37개 기업 chunk의 embedding만 생성합니다.")
    parser.add_argument("--model", default=os.environ.get("EMBEDDING_MODEL"))
    parser.add_argument("--label", required=True, help="예: baseline 또는 finetuned-v1")
    parser.add_argument("--chunks", type=Path, default=Path("/input/company_chunks.pkl"))
    parser.add_argument("--manifest", type=Path, default=Path("/input/company_ingest_manifest.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("/output"))
    parser.add_argument("--batch-size", type=int, default=8)
    args = parser.parse_args()
    if not args.model:
        parser.error("--model 또는 EMBEDDING_MODEL이 필요합니다.")
    if not args.label.replace("-", "").replace("_", "").isalnum():
        parser.error("--label은 영문, 숫자, '-'와 '_'만 사용할 수 있습니다.")

    chunks, fingerprint = load_and_validate_chunks(args.chunks, args.manifest)
    model = SentenceTransformer(args.model, device="cuda" if torch.cuda.is_available() else "cpu")
    embeddings = np.asarray(model.encode(
        [chunk["text"] for chunk in chunks],
        normalize_embeddings=True,
        show_progress_bar=True,
        batch_size=args.batch_size,
    ))
    if embeddings.shape != (EXPECTED_CHUNKS, EXPECTED_DIMENSIONS):
        raise ValueError(f"embedding shape가 {(EXPECTED_CHUNKS, EXPECTED_DIMENSIONS)}가 아닙니다: {embeddings.shape}")
    if not np.isfinite(embeddings).all():
        raise ValueError("embedding에 NaN 또는 infinite 값이 있습니다.")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    embedding_path = args.output_dir / f"{args.label}_embeddings.pkl"
    metadata_path = args.output_dir / f"{args.label}_metadata.json"
    atomic_pickle(embedding_path, embeddings)
    metadata = {
        "label": args.label,
        "embedding_model": args.model,
        "embedding_dimensions": EXPECTED_DIMENSIONS,
        "normalize_embeddings": True,
        "document_count": EXPECTED_DOCUMENTS,
        "chunk_count": EXPECTED_CHUNKS,
        "embedding_shape": list(embeddings.shape),
        "chunk_fingerprint_sha256": fingerprint,
        "embedding_file": embedding_path.name,
    }
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
