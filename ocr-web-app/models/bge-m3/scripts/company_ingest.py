import json
import pickle
from pathlib import Path

import numpy as np
import torch
from pypdf import PdfReader
from sentence_transformers import SentenceTransformer


# =========================================================
# 경로 설정
# =========================================================

BASE_DIR = Path(__file__).resolve().parent.parent

COMPANY_DIR = BASE_DIR / "data" / "company_documents"
DOCUMENTS_DIR = COMPANY_DIR / "documents"
METADATA_PATH = COMPANY_DIR / "metadata" / "document_catalog.json"

PROCESSED_DIR = COMPANY_DIR / "processed"
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)


# =========================================================
# 청킹 설정
# =========================================================

CHUNK_SIZE = 600
CHUNK_OVERLAP = 120


def split_text(text, chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    chunks = []

    start = 0

    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end].strip()

        if chunk:
            chunks.append(chunk)

        if end >= len(text):
            break

        start = end - overlap

    return chunks


# =========================================================
# Metadata 로드
# =========================================================

with open(METADATA_PATH, "r", encoding="utf-8") as f:
    catalog = json.load(f)

documents_metadata = catalog["documents"]

print("회사:", catalog["company"])
print("문서 수:", len(documents_metadata))


# filename → metadata
metadata_map = {
    doc["filename"]: doc
    for doc in documents_metadata
}


# =========================================================
# PDF → Chunk
# =========================================================

all_chunks = []

for pdf_path in sorted(DOCUMENTS_DIR.glob("*.pdf")):

    print(f"\n처리 중: {pdf_path.name}")

    metadata = metadata_map.get(pdf_path.name)

    if metadata is None:
        print("⚠️ metadata 없음:", pdf_path.name)
        continue

    reader = PdfReader(pdf_path)

    document_chunk_index = 0   # ⭐ 문서 전체 기준

    for page_number, page in enumerate(reader.pages, start=1):

        text = page.extract_text()

        if not text:
            continue

        page_chunks = split_text(text)

        for chunk_text in page_chunks:

            chunk_data = {
                "text": chunk_text,
                "doc_id": metadata["doc_id"],
                "title": metadata["title"],
                "owner": metadata["owner"],
                "security": metadata["security"],
                "version": metadata["version"],
                "effective_date": metadata["effective_date"],
                "tags": metadata["tags"],
                "filename": metadata["filename"],
                "page": page_number,

                # ⭐ 페이지마다 0으로 초기화하지 않음
                "chunk_index": document_chunk_index,
            }

            all_chunks.append(chunk_data)

            document_chunk_index += 1


print("\n==============================")
print("PDF 처리 완료")
print("총 chunk 수:", len(all_chunks))
print("==============================")


# =========================================================
# BGE-M3
# =========================================================

device = "cuda" if torch.cuda.is_available() else "cpu"

print("사용 장치:", device)
print("BGE-M3 로딩 중...")

model = SentenceTransformer(
    "BAAI/bge-m3",
    device=device
)

texts = [
    chunk["text"]
    for chunk in all_chunks
]

print("Embedding 생성 중...")

embeddings = model.encode(
    texts,
    normalize_embeddings=True,
    show_progress_bar=True,
    batch_size=8,
)

embeddings = np.asarray(embeddings)


# =========================================================
# 저장
# =========================================================

chunks_pickle_path = PROCESSED_DIR / "company_chunks.pkl"
embeddings_path = PROCESSED_DIR / "company_embeddings.pkl"
chunks_json_path = PROCESSED_DIR / "company_chunks.json"


with open(chunks_pickle_path, "wb") as f:
    pickle.dump(all_chunks, f)

with open(embeddings_path, "wb") as f:
    pickle.dump(embeddings, f)

with open(chunks_json_path, "w", encoding="utf-8") as f:
    json.dump(
        all_chunks,
        f,
        ensure_ascii=False,
        indent=2,
    )


print("\n✅ 저장 완료")

print("chunks:")
print(chunks_pickle_path)

print("embeddings:")
print(embeddings_path)

print("JSON:")
print(chunks_json_path)

print("\nembedding shape:", embeddings.shape)