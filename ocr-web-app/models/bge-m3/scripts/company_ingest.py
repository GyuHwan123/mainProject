from __future__ import annotations

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
    raise ImportError("기업문서 표 추출에는 PyMuPDF가 필요합니다: pip install pymupdf") from exc

EMBEDDING_MODEL = "BAAI/bge-m3"
EMBEDDING_DIMENSION = 1024
CHUNK_SIZE = 600
CHUNK_OVERLAP = 120
BASE_DIR = Path(__file__).resolve().parent.parent
COMPANY_DIR = BASE_DIR / "data" / "company_documents"
DOCUMENTS_DIR = COMPANY_DIR / "documents"
METADATA_PATH = COMPANY_DIR / "metadata" / "document_catalog.json"
PROCESSED_DIR = COMPANY_DIR / "processed"
CHUNKS_PATH = PROCESSED_DIR / "company_chunks.pkl"
EMBEDDINGS_PATH = PROCESSED_DIR / "company_embeddings.pkl"
CHUNKS_JSON_PATH = PROCESSED_DIR / "company_chunks.json"
MANIFEST_PATH = PROCESSED_DIR / "company_ingest_manifest.json"


def split_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    chunks, start = [], 0
    while start < len(text):
        end = start + chunk_size
        if chunk := text[start:end].strip():
            chunks.append(chunk)
        if end >= len(text):
            break
        start = end - overlap
    return chunks


def _clean_cell(value: object) -> str:
    return " ".join(str(value or "").split())


def _table_rows(table: object) -> list[list[str]]:
    rows = []
    for raw_row in table.extract() or []:
        row = [_clean_cell(cell) for cell in raw_row]
        if any(row):
            rows.append(row)
    return rows


def _serialize_table_rows(rows: list[list[str]]) -> list[str]:
    if len(rows) < 2 or not any(rows[0]):
        return []
    headers, rendered = rows[0], []
    for row in rows[1:]:
        if row == headers:
            continue
        fields = []
        for index, value in enumerate(row):
            if value:
                header = headers[index] if index < len(headers) else ""
                fields.append(f"{header or f'열 {index + 1}'}: {value}")
        if fields:
            rendered.append("[표 행] " + " | ".join(fields))
    return rendered


def _remove_flattened_table(body_text: str, rows: list[list[str]]) -> str:
    cells = [cell for row in rows for cell in row if cell]
    pattern = r"\s+".join(r"\s+".join(re.escape(word) for word in cell.split()) for cell in cells)
    cleaned, count = re.subn(pattern, "\n", body_text, count=1, flags=re.MULTILINE)
    if cells and count == 0:
        raise ValueError("검출한 표를 본문에서 제거하지 못해 중복 방지를 위해 중단합니다.")
    return cleaned


def extract_page_text(pypdf_page: object, pymupdf_page: object) -> str:
    body_text = pypdf_page.extract_text() or ""
    structured_rows = []
    for table in pymupdf_page.find_tables().tables:
        rows = _table_rows(table)
        rendered = _serialize_table_rows(rows)
        if rendered:
            body_text = _remove_flattened_table(body_text, rows)
            structured_rows.extend(rendered)
    body_text = re.sub(r"\n{3,}", "\n\n", body_text).strip()
    return "\n".join(part for part in (body_text, *structured_rows) if part)


def build_company_chunks() -> tuple[list[dict], int]:
    catalog = json.loads(METADATA_PATH.read_text(encoding="utf-8"))
    metadata_map = {document["filename"]: document for document in catalog["documents"]}
    pdf_paths = sorted(DOCUMENTS_DIR.glob("*.pdf"))
    if len(metadata_map) != 18 or len(pdf_paths) != 18:
        raise ValueError(f"기업문서는 metadata={len(metadata_map)}, pdf={len(pdf_paths)}입니다. 각각 18개여야 합니다.")
    all_chunks, table_row_count, doc_ids = [], 0, set()
    for pdf_path in pdf_paths:
        metadata = metadata_map.get(pdf_path.name)
        if metadata is None:
            raise ValueError(f"metadata가 없는 PDF입니다: {pdf_path.name}")
        doc_ids.add(metadata["doc_id"])
        reader, document = PdfReader(pdf_path), fitz.open(pdf_path)
        try:
            chunk_index = 0
            for page_number, page in enumerate(reader.pages, 1):
                text = extract_page_text(page, document[page_number - 1])
                table_row_count += text.count("[표 행]")
                for content in split_text(text) if text else []:
                    all_chunks.append({
                        "text": content, "doc_id": metadata["doc_id"], "title": metadata["title"],
                        "owner": metadata["owner"], "security": metadata["security"],
                        "version": metadata["version"], "effective_date": metadata["effective_date"],
                        "tags": metadata["tags"], "filename": metadata["filename"],
                        "page": page_number, "chunk_index": chunk_index,
                    })
                    chunk_index += 1
        finally:
            document.close()
    if len(doc_ids) != 18:
        raise ValueError(f"처리된 고유 기업문서가 {len(doc_ids)}개입니다.")
    return all_chunks, table_row_count


def build_embeddings(chunks: list[dict]) -> np.ndarray:
    model = SentenceTransformer(EMBEDDING_MODEL, device="cuda" if torch.cuda.is_available() else "cpu")
    embeddings = np.asarray(model.encode(
        [chunk["text"] for chunk in chunks], normalize_embeddings=True,
        show_progress_bar=True, batch_size=8,
    ))
    if embeddings.shape != (len(chunks), EMBEDDING_DIMENSION) or not np.isfinite(embeddings).all():
        raise ValueError(f"유효하지 않은 embedding shape입니다: {embeddings.shape}")
    return embeddings


def save_results(chunks: list[dict], embeddings: np.ndarray, table_row_count: int) -> dict:
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    with CHUNKS_PATH.open("wb") as file:
        pickle.dump(chunks, file)
    with EMBEDDINGS_PATH.open("wb") as file:
        pickle.dump(embeddings, file)
    CHUNKS_JSON_PATH.write_text(json.dumps(chunks, ensure_ascii=False, indent=2), encoding="utf-8")
    table_chunks = [chunk for chunk in chunks if "[표 행]" in chunk["text"]]
    samples = [line for chunk in table_chunks for line in chunk["text"].splitlines() if line.startswith("[표 행]")][:3]
    manifest = {
        "embedding_model": EMBEDDING_MODEL, "embedding_dimension": EMBEDDING_DIMENSION,
        "normalize_embeddings": True, "document_count": len({c["doc_id"] for c in chunks}),
        "chunk_count": len(chunks), "embedding_shape": list(embeddings.shape),
        "table_row_count": table_row_count, "table_chunk_count": len(table_chunks),
        "table_samples": samples,
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main() -> None:
    chunks, table_row_count = build_company_chunks()
    embeddings = build_embeddings(chunks)
    print(json.dumps(save_results(chunks, embeddings, table_row_count), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
