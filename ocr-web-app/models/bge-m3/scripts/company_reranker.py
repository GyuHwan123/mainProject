import pickle
from pathlib import Path

import numpy as np
import torch
from sentence_transformers import SentenceTransformer
from FlagEmbedding import FlagReranker


BASE_DIR = Path(__file__).resolve().parent.parent
PROCESSED_DIR = (
    BASE_DIR
    / "data"
    / "company_documents"
    / "processed"
)

CHUNKS_PATH = PROCESSED_DIR / "company_chunks.pkl"
EMBEDDINGS_PATH = PROCESSED_DIR / "company_embeddings.pkl"


# =========================
# 데이터 로드
# =========================

with open(CHUNKS_PATH, "rb") as f:
    chunks = pickle.load(f)

with open(EMBEDDINGS_PATH, "rb") as f:
    embeddings = pickle.load(f)

embeddings = np.asarray(embeddings)

print("chunk 수:", len(chunks))
print("embedding shape:", embeddings.shape)


# =========================
# Dense Retriever
# =========================

device = "cuda" if torch.cuda.is_available() else "cpu"

print("사용 장치:", device)
print("BGE-M3 로딩 중...")

model = SentenceTransformer(
    "BAAI/bge-m3",
    device=device,
)

print("✅ BGE-M3 로드 완료")


# =========================
# Reranker
# =========================

print("Reranker 로딩 중...")

reranker = FlagReranker(
    "BAAI/bge-reranker-v2-m3",
    use_fp16=torch.cuda.is_available(),
)

print("✅ Reranker 로드 완료")


# =========================
# 검색
# =========================

while True:
    query = input(
        "\n질문을 입력하세요 (종료: exit): "
    ).strip()

    if query.lower() == "exit":
        break

    # 1. Dense 검색
    query_embedding = model.encode(
        query,
        normalize_embeddings=True,
    )

    dense_scores = embeddings @ query_embedding

    candidate_k = min(10, len(chunks))

    candidate_indices = np.argsort(
        dense_scores
    )[::-1][:candidate_k]

    print("\n" + "=" * 80)
    print("Dense 검색 Top-5")
    print("=" * 80)

    for rank, idx in enumerate(
        candidate_indices[:5],
        start=1,
    ):
        chunk = chunks[idx]

        print(
            f"\n[{rank}위] "
            f"{chunk['title']} "
            f"| chunk={idx} "
            f"| score={dense_scores[idx]:.4f}"
        )

    # 2. Reranker 입력
    pairs = [
        [query, chunks[idx]["text"]]
        for idx in candidate_indices
    ]

    rerank_scores = reranker.compute_score(
        pairs,
        normalize=True,
    )

    if not isinstance(
        rerank_scores,
        (list, tuple, np.ndarray),
    ):
        rerank_scores = [rerank_scores]

    # 3. 재정렬
    reranked = sorted(
        zip(
            candidate_indices,
            rerank_scores,
        ),
        key=lambda x: x[1],
        reverse=True,
    )

    # 4. 최종 Top-5
    print("\n" + "=" * 80)
    print("Reranker 적용 Top-5")
    print("=" * 80)

    for rank, (idx, rerank_score) in enumerate(
        reranked[:5],
        start=1,
    ):
        chunk = chunks[idx]

        print(f"\n[{rank}위]")
        print(f"Reranker 점수 : {rerank_score:.4f}")
        print(f"Dense 점수    : {dense_scores[idx]:.4f}")
        print(f"문서 ID       : {chunk['doc_id']}")
        print(f"문서명        : {chunk['title']}")
        print(f"담당 부서     : {chunk['owner']}")
        print(f"페이지        : {chunk['page']}")
        print(f"원본 chunk    : {idx}")

        print("\n내용:")
        print(chunk["text"])

        print("-" * 80)