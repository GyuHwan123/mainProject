from __future__ import annotations

import pickle
import sys
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import numpy as np
import psycopg2


EMBEDDING_DIMENSION = 1024
BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BASE_DIR.parent.parent
PROCESSED_DIR = BASE_DIR / "data" / "company_documents" / "processed"
CHUNKS_PATH = PROCESSED_DIR / "company_chunks.pkl"
EMBEDDINGS_PATH = PROCESSED_DIR / "company_embeddings.pkl"


def load_processed_data() -> tuple[list[dict], np.ndarray]:
	with CHUNKS_PATH.open("rb") as chunks_file:
		chunks = pickle.load(chunks_file)
	with EMBEDDINGS_PATH.open("rb") as embeddings_file:
		embeddings = np.asarray(pickle.load(embeddings_file))

	if not isinstance(chunks, list):
		raise ValueError("company_chunks.pkl must contain a list")
	if embeddings.ndim != 2:
		raise ValueError(f"embeddings must be a 2D array, got shape {embeddings.shape}")
	if len(chunks) != embeddings.shape[0]:
		raise ValueError(
			f"chunk count ({len(chunks)}) does not match embedding rows ({embeddings.shape[0]})"
		)
	if embeddings.shape[1] != EMBEDDING_DIMENSION:
		raise ValueError(
			f"embedding dimension must be {EMBEDDING_DIMENSION}, got {embeddings.shape[1]}"
		)
	if not np.isfinite(embeddings).all():
		raise ValueError("embeddings contain NaN or infinite values")

	return chunks, embeddings


def get_database_url() -> str:
	sys.path.insert(0, str(PROJECT_ROOT / "backend"))
	from app.core.config import settings

	parsed = urlparse(settings.DATABASE_URL)
	scheme = parsed.scheme.replace("+psycopg2", "")
	return urlunparse(
		(scheme, parsed.netloc, parsed.path, parsed.params, parsed.query, parsed.fragment)
	)


def document_payload(chunk: dict) -> tuple:
	required_fields = (
		"doc_id",
		"title",
		"owner",
		"security",
		"version",
		"effective_date",
		"filename",
		"tags",
	)
	missing_fields = [field for field in required_fields if field not in chunk]
	if missing_fields:
		raise ValueError(f"missing document fields: {', '.join(missing_fields)}")

	tags = chunk["tags"]
	if not isinstance(tags, (list, tuple)):
		raise ValueError("tags must be a list or tuple")

	return tuple(chunk[field] for field in required_fields[:-1]) + (list(tags),)


def vector_literal(embedding: np.ndarray) -> str:
	return "[" + ",".join(format(float(value), ".17g") for value in embedding) + "]"


def ingest() -> None:
	chunks, embeddings = load_processed_data()

	stored_documents = 0
	stored_chunks = 0
	skipped_duplicates = 0
	failures = 0
	document_ids: dict[str, str] = {}

	connection = psycopg2.connect(get_database_url())
	try:
		with connection:
			with connection.cursor() as cursor:
				for chunk, embedding in zip(chunks, embeddings):
					cursor.execute("savepoint company_chunk")
					try:
						if not isinstance(chunk, dict):
							raise ValueError("each chunk must be a dictionary")

						doc_id = str(chunk["doc_id"])
						created_document = False
						document_id = document_ids.get(doc_id)
						if document_id is None:
							cursor.execute(
								"""
								insert into public.rag_documents
									(doc_id, title, owner, security, version,
									 effective_date, filename, tags)
								values (%s, %s, %s, %s, %s, %s, %s, %s)
								on conflict (doc_id) do nothing
								returning id
								""",
								document_payload(chunk),
							)
							document_row = cursor.fetchone()
							if document_row is not None:
								created_document = True
								document_id = str(document_row[0])
							else:
								cursor.execute(
									"""
									select id
									from public.rag_documents
									where doc_id = %s
									""",
									(doc_id,),
								)
								existing_document = cursor.fetchone()
								if existing_document is None:
									raise RuntimeError(f"document not found after conflict: {doc_id}")
								document_id = str(existing_document[0])

						cursor.execute(
                            """
                            insert into public.rag_chunks
                                (document_id, chunk_index, page_number, content, embedding)
                            values (%s, %s, %s, %s, %s::vector)
                            on conflict (document_id, chunk_index) do nothing
                            returning id
                            """,
                            (
                                document_id,
                                int(chunk["chunk_index"]),
                                int(chunk["page"]),
                                str(chunk["text"]),
                                vector_literal(embedding),
                            ),
                        )
						if cursor.fetchone() is None:
							skipped_duplicates += 1
						else:
							stored_chunks += 1
						if document_id is not None:
							document_ids[doc_id] = document_id
						if created_document:
							stored_documents += 1
						cursor.execute("release savepoint company_chunk")
					except Exception as exc:
						cursor.execute("rollback to savepoint company_chunk")
						cursor.execute("release savepoint company_chunk")
						failures += 1
						print(f"적재 실패: {exc}")

		print("\n적재 완료")
		print(f"저장된 문서 수: {stored_documents}")
		print(f"저장된 chunk 수: {stored_chunks}")
		print(f"중복으로 건너뛴 수: {skipped_duplicates}")
		print(f"실패 수: {failures}")
		print(f"embedding dimension: {embeddings.shape[1]}")
	finally:
		connection.close()


if __name__ == "__main__":
	ingest()
