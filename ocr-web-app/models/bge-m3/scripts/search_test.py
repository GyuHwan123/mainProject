import pickle
from pathlib import Path
import numpy as np

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

CHUNKS_PATH = DATA_DIR / "chunks.pkl"
EMBEDDINGS_PATH = DATA_DIR / "paragraph_overlap_embeddings.pkl"

print("chunks 경로:", CHUNKS_PATH)
print("embeddings 경로:", EMBEDDINGS_PATH)

# 1. chunks 불러오기
with open(CHUNKS_PATH, "rb") as f:
    chunks = pickle.load(f)

# 2. embeddings 불러오기
with open(EMBEDDINGS_PATH, "rb") as f:
    embeddings = pickle.load(f)

print("\n===== 로드 결과 =====")
print("chunks 타입:", type(chunks))
print("chunks 개수:", len(chunks))

print("embeddings 타입:", type(embeddings))

if hasattr(embeddings, "shape"):
    print("embeddings shape:", embeddings.shape)
else:
    print("embeddings 개수:", len(embeddings))

print("\n===== 첫 번째 chunk =====")
print(chunks[0])

print("\n✅ 기존 데이터 로드 성공")