from sentence_transformers import SentenceTransformer
import torch

print("CUDA 사용 가능:", torch.cuda.is_available())

if torch.cuda.is_available():
    print("GPU:", torch.cuda.get_device_name(0))

device = "cuda" if torch.cuda.is_available() else "cpu"

print("\nBGE-M3 로딩 중...")

model = SentenceTransformer(
    "BAAI/bge-m3",
    device=device
)

print("✅ BGE-M3 로드 완료")
print("사용 장치:", device)
print("임베딩 차원:", model.get_sentence_embedding_dimension())