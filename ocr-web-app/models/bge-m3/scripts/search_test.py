import pickle
from pathlib import Path
import numpy as np
import torch
from sentence_transformers import SentenceTransformer

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

CHUNKS_PATH = DATA_DIR / "chunks.pkl"
EMBEDDINGS_PATH = DATA_DIR / "paragraph_overlap_embeddings.pkl"

# =========================
# 1. 기존 데이터 로드
# =========================
with open(CHUNKS_PATH, "rb") as f:
    chunks = pickle.load(f)

with open(EMBEDDINGS_PATH, "rb") as f:
    embeddings = pickle.load(f)

print("chunks 개수:", len(chunks))
print("embeddings shape:", embeddings.shape)

# =========================
# 2. BGE-M3 로드
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
# 3. 질문 입력
# =========================
query = input("\n질문을 입력하세요: ")

# 질문 임베딩
query_embedding = model.encode(
    query,
    normalize_embeddings=True
)

# =========================
# 4. 기존 임베딩 정규화
# =========================
doc_embeddings = np.asarray(embeddings)

norms = np.linalg.norm(
    doc_embeddings,
    axis=1,
    keepdims=True
)

doc_embeddings = doc_embeddings / np.clip(norms, 1e-12, None)

# =========================
# 5. Cosine Similarity
# =========================
scores = doc_embeddings @ query_embedding

# =========================
# 6. Top-5
# =========================
top_k = 5
top_indices = np.argsort(scores)[::-1][:top_k]

print("\n===== Top-5 검색 결과 =====")

for rank, idx in enumerate(top_indices, start=1):
    print(f"\n[{rank}위]")
    print(f"청크 번호: {idx}")
    print(f"유사도: {scores[idx]:.4f}")
    print("내용:")
    print(chunks[idx])
    print("-" * 80)