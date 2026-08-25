import json
import pickle
import re
from pathlib import Path

import numpy as np
import torch
from pypdf import PdfReader
from sentence_transformers import SentenceTransformer


# =========================================================
# 경로
# =========================================================

BASE_DIR = Path(__file__).resolve().parent.parent

COMPANY_DIR = BASE_DIR / "data" / "company_documents"
DOCUMENTS_DIR = COMPANY_DIR / "documents"
METADATA_PATH = COMPANY_DIR / "metadata" / "document_catalog.json"

PROCESSED_DIR = COMPANY_DIR / "processed_v2"
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)


# =========================================================
# 설정
# =========================================================

MAX_CHUNK_SIZE = 900
LONG_SECTION_OVERLAP = 120


# =========================================================
# 긴 조항 추가 분할
# =========================================================

def split_long_text(text, max_size=MAX_CHUNK_SIZE, overlap=LONG_SECTION_OVERLAP):
    if len(text) <= max_size:
        return [text.strip()]

    chunks = []
    start = 0

    while start < len(text):
        end = start + max_size
        chunk = text[start:end].strip()

        if chunk:
            chunks.append(chunk)

        if end >= len(text):
            break

        start = end - overlap

    return chunks


# =========================================================
# 조항 단위 청킹
# =========================================================

def split_by_article(text):
    """
    '제1조 (...)', '제8조 (...)' 같은 조항을 기준으로 분리.
    제목과 본문을 같은 chunk에 유지한다.
    """

    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # 제1조, 제2조 ... 시작 위치 탐색
    pattern = re.compile(
        r"(?=제\s*\d+\s*조\s*(?:\([^)]*\))?)"
    )

    parts = pattern.split(text)

    sections = []

    for part in parts:
        part = part.strip()

        if not part:
            continue

        # 너무 긴 조항만 추가 분할
        sections.extend(split_long_text(part))

    return sections

def clean_page_text(text):
    lines = text.splitlines()

    cleaned = []

    for line in lines:
        stripped = line.strip()

        # 반복 회사명 제거
        if stripped == "네오웍스테크(주)":
            continue

        # 페이지 번호 형태 제거: 1 /, 2 / ...
        if re.fullmatch(r"\d+\s*/", stripped):
            continue

        cleaned.append(line)

    return "\n".join(cleaned)
# =========================================================
# Metadata
# =========================================================

with open(METADATA_PATH, "r", encoding="utf-8") as f:
    catalog = json.load(f)

documents_metadata = catalog["documents"]

print("회사:", catalog["company"])
print("문서 수:", len(documents_metadata))

metadata_map = {
    doc["filename"]: doc
    for doc in documents_metadata
}


# =========================================================
# PDF → 문서 전체 기준 조항 단위 Chunk
# =========================================================

all_chunks = []

for pdf_path in sorted(DOCUMENTS_DIR.glob("*.pdf")):

    print(f"\n처리 중: {pdf_path.name}")

    metadata = metadata_map.get(pdf_path.name)

    if metadata is None:
        print("⚠️ metadata 없음:", pdf_path.name)
        continue

    reader = PdfReader(pdf_path)

    # -----------------------------------------
    # 1. 문서의 모든 페이지를 먼저 합친다.
    # -----------------------------------------

    document_parts = []

    for page_number, page in enumerate(reader.pages, start=1):

        text = page.extract_text()

        if not text:
            continue

        text = clean_page_text(text)

        # 페이지 추적용 마커
        document_parts.append(
            f"\n[[PAGE:{page_number}]]\n{text}"
        )

    full_text = "\n".join(document_parts)

    # -----------------------------------------
    # 2. 문서 전체를 조항 기준으로 분할
    # -----------------------------------------

    document_chunks = split_by_article(full_text)

    document_chunk_index = 0

    for chunk_text in document_chunks:

        # chunk 안에 포함된 첫 페이지 번호 추출
        page_match = re.search(
            r"\[\[PAGE:(\d+)\]\]",
            chunk_text
        )

        page_number = (
            int(page_match.group(1))
            if page_match
            else 1
        )

        # 페이지 마커 제거
        clean_text = re.sub(
            r"\[\[PAGE:\d+\]\]",
            "",
            chunk_text
        ).strip()

        if not clean_text:
            continue

        # 조항명 추출
        section_match = re.search(
            r"(제\s*\d+\s*조\s*(?:\([^)]*\))?)",
            clean_text
        )

        section = (
            section_match.group(1).strip()
            if section_match
            else None
        )

        chunk_data = {
            "text": clean_text,

            "doc_id": metadata["doc_id"],
            "title": metadata["title"],
            "owner": metadata["owner"],
            "security": metadata["security"],
            "version": metadata["version"],
            "effective_date": metadata["effective_date"],
            "tags": metadata["tags"],
            "filename": metadata["filename"],

            "page": page_number,
            "chunk_index": document_chunk_index,
            "section": section,
        }

        all_chunks.append(chunk_data)

        document_chunk_index += 1


# =========================================================
# 중복 검사
# =========================================================

seen = set()
duplicates = []

for chunk in all_chunks:
    key = (
        chunk["doc_id"],
        chunk["chunk_index"],
    )

    if key in seen:
        duplicates.append(key)

    seen.add(key)

print("중복 chunk index:", duplicates)


# =========================================================
# BGE-M3
# =========================================================

device = "cuda" if torch.cuda.is_available() else "cpu"

print("사용 장치:", device)
print("BGE-M3 로딩 중...")

model = SentenceTransformer(
    "BAAI/bge-m3",
    device=device,
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

chunks_pickle_path = PROCESSED_DIR / "company_chunks_v2.pkl"
embeddings_path = PROCESSED_DIR / "company_embeddings_v2.pkl"
chunks_json_path = PROCESSED_DIR / "company_chunks_v2.json"


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


print("\n✅ V2 저장 완료")
print("chunks:", chunks_pickle_path)
print("embeddings:", embeddings_path)
print("JSON:", chunks_json_path)
print("embedding shape:", embeddings.shape)


# =========================================================
# 샘플 확인
# =========================================================

print("\n===== 조항 청킹 샘플 =====")

for chunk in all_chunks[:10]:
    print(
        f"\n[{chunk['doc_id']}] "
        f"chunk={chunk['chunk_index']} "
        f"page={chunk['page']} "
        f"section={chunk['section']}"
    )
    print(chunk["text"][:300])
    print("-" * 60)