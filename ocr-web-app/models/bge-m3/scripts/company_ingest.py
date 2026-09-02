import json
import pickle
import re
from pathlib import Path

import numpy as np
import torch
from pypdf import PdfReader
from sentence_transformers import SentenceTransformer

try:
    import fitz
except ImportError as exc:
    raise ImportError(
        "기업문서 표 구조 추출에는 PyMuPDF가 필요합니다. "
        "'pip install pymupdf'로 설치한 뒤 다시 실행하세요."
    ) from exc


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


def _clean_cell(value):
    return " ".join(str(value or "").split())


def _table_rows(table):
    rows = []
    for raw_row in table.extract() or []:
        row = [_clean_cell(cell) for cell in raw_row]
        if any(row):
            rows.append(row)
    return rows


def _serialize_table_rows(rows):
    """Render each data row with explicit column headers."""
    if len(rows) < 2:
        return []

    headers = rows[0]
    if not any(headers):
        return []

    rendered = []
    for row in rows[1:]:
        if row == headers:
            continue
        fields = []
        for column_index, value in enumerate(row):
            if not value:
                continue
            header = headers[column_index] if column_index < len(headers) else ""
            header = header or f"열 {column_index + 1}"
            fields.append(f"{header}: {value}")
        if fields:
            rendered.append("[표 행] " + " | ".join(fields))
    return rendered


def _cell_pattern(value):
    return r"\s+".join(re.escape(word) for word in str(value or "").split())


def _remove_flattened_table(body_text, rows):
    """Remove one pypdf-rendered copy of a detected table."""
    cells = [cell for row in rows for cell in row if cell]
    if not cells:
        return body_text

    pattern = r"\s+".join(_cell_pattern(cell) for cell in cells)
    cleaned, count = re.subn(pattern, "\n", body_text, count=1, flags=re.MULTILINE)
    if count == 0:
        raise ValueError(
            "PyMuPDF가 표를 검출했지만 pypdf 본문에서 같은 표 텍스트를 제거하지 못했습니다. "
            "중복 표 텍스트 생성을 막기 위해 인제스트를 중단합니다."
        )
    return cleaned


def extract_page_text(pypdf_page, pymupdf_page):
    """Combine the existing pypdf body with structured table rows."""
    body_text = pypdf_page.extract_text() or ""
    structured_rows = []

    for table in pymupdf_page.find_tables().tables:
        rows = _table_rows(table)
        rendered_rows = _serialize_table_rows(rows)
        if not rendered_rows:
            continue
        body_text = _remove_flattened_table(body_text, rows)
        structured_rows.extend(rendered_rows)

    body_text = re.sub(r"\n{3,}", "\n\n", body_text).strip()
    if not structured_rows:
        return body_text
    return "\n".join(part for part in (body_text, *structured_rows) if part)


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
    pymupdf_document = fitz.open(pdf_path)

    document_chunk_index = 0   # ⭐ 문서 전체 기준

    for page_number, page in enumerate(reader.pages, start=1):

        text = extract_page_text(page, pymupdf_document[page_number - 1])

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

    pymupdf_document.close()


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
