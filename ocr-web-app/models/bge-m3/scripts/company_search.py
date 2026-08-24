import pickle
from pathlib import Path

import numpy as np
import torch
from sentence_transformers import SentenceTransformer


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
# BGE-M3
# =========================

device = "cuda" if torch.cuda.is_available() else "cpu"

print("사용 장치:", device)
print("BGE-M3 로딩 중...")

model = SentenceTransformer(
    "BAAI/bge-m3",
    device=device
)

print("✅ BGE-M3 로드 완료")


# =========================
# 검색
# =========================

while True:

    query = input(
        "\n질문을 입력하세요 (종료: exit): "
    ).strip()

    if query.lower() == "exit":
        break

    query_embedding = model.encode(
        query,
        normalize_embeddings=True
    )

    # company_embeddings도
    # ingest할 때 normalize_embeddings=True로 생성했으므로
    # 내적으로 cosine similarity 계산 가능
    scores = embeddings @ query_embedding

    top_k = min(5, len(chunks))

    top_indices = np.argsort(scores)[::-1][:top_k]

    print("\n" + "=" * 80)
    print("Top-5 검색 결과")
    print("=" * 80)

    for rank, idx in enumerate(top_indices, start=1):

        chunk = chunks[idx]

        print(f"\n[{rank}위]")
        print(f"유사도      : {scores[idx]:.4f}")
        print(f"문서 ID     : {chunk['doc_id']}")
        print(f"문서명      : {chunk['title']}")
        print(f"담당 부서   : {chunk['owner']}")
        print(f"보안 등급   : {chunk['security']}")
        print(f"버전        : {chunk['version']}")
        print(f"시행일      : {chunk['effective_date']}")
        print(f"페이지      : {chunk['page']}")
        print(f"파일        : {chunk['filename']}")
        print(f"태그        : {', '.join(chunk['tags'])}")

        print("\n내용:")
        print(chunk["text"])

        print("-" * 80)