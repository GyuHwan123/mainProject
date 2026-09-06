"""Generate configured BGE-M3 embeddings for the frozen 115 company v3 chunks.

Offline, no DB client, no model fallback, and no output overwrite. Run with the
backend Python environment, which already contains sentence-transformers/torch.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import pickle
import sys
from typing import Any

import numpy as np

BASE_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BASE_DIR.parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))
from app.core.config import settings
from app.services.embedding_model import load_embedding_model

COMPANY_DIR = BASE_DIR / "data/company_documents"
CHUNK_FILE = COMPANY_DIR / "processed_v3/company_chunks_v3.pkl"
MANIFEST_FILE = COMPANY_DIR / "processed_v3/company_ingest_manifest_v3.json"
OUTPUT_DIR = COMPANY_DIR / "embedding_variants_v3"
MODEL = settings.RAG_EMBEDDING_MODEL
LABEL = "baseline-v3" if MODEL == "BAAI/bge-m3" else "finetuned-v3"
EXPECTED_FINGERPRINT = "4cd335078424292478a5a57493b4472b29434fb88e533bfd718809fb5681b15d"
EXPECTED_SHAPE = (115, 1024)


def sha256_file(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(8 * 1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def chunk_fingerprint(chunks: list[dict]) -> str:
    stable = [{"doc_id": c["doc_id"], "chunk_index": int(c["chunk_index"]),
               "page": int(c["page"]), "text": c["text"]} for c in chunks]
    return hashlib.sha256(json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate_chunks(chunks: Any) -> dict:
    if not isinstance(chunks, list) or len(chunks) != 115:
        raise ValueError("Exactly 115 ordered v3 chunks required")
    keys = []
    for c in chunks:
        if not isinstance(c, dict) or not isinstance(c.get("text"), str) or not c["text"].strip():
            raise ValueError("Missing/empty/non-string text")
        if not isinstance(c.get("doc_id"), str) or not c["doc_id"]:
            raise ValueError("Missing doc_id")
        if not isinstance(c.get("chunk_index"), int) or c["chunk_index"] < 0:
            raise ValueError("Invalid chunk_index")
        if not isinstance(c.get("page"), int) or c["page"] < 1:
            raise ValueError("Invalid source page")
        if c.get("content") != c["text"]:
            raise ValueError("content alias differs from text")
        keys.append((c["doc_id"], c["chunk_index"]))
    if len(set(keys)) != 115 or len({k[0] for k in keys}) != 18:
        raise ValueError("Duplicate keys or wrong document count")
    for doc_id in {k[0] for k in keys}:
        indexes = [k[1] for k in keys if k[0] == doc_id]
        if indexes != list(range(len(indexes))):
            raise ValueError(f"Non-contiguous/inverted input indexes: {doc_id}")
    return {"document_count": 18, "chunk_count": 115, "missing_or_empty_text": 0,
            "duplicate_keys": 0, "chunk_fingerprint_sha256": chunk_fingerprint(chunks)}


def load_chunks() -> tuple[list[dict], dict]:
    with CHUNK_FILE.open("rb") as file:
        chunks = pickle.load(file)
    validation = validate_chunks(chunks)
    manifest = json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
    if manifest.get("chunking_version") != "v3" or manifest["stats"]["chunk_count"] != 115:
        raise ValueError("Wrong chunk manifest")
    if validation["chunk_fingerprint_sha256"] != EXPECTED_FINGERPRINT or manifest.get("chunk_fingerprint_sha256") != EXPECTED_FINGERPRINT:
        raise ValueError("Frozen v3 fingerprint mismatch")
    return chunks, validation


def protected_hashes() -> dict[str, str]:
    folders = [COMPANY_DIR / name for name in ("processed", "processed_v2", "processed_v3", "embedding_variants")]
    folders += [BASE_DIR / "finetuned", BASE_DIR / "model_checkpoints"]
    paths = [p for folder in folders for p in folder.rglob("*") if p.is_file()]
    paths += [BASE_DIR / "scripts" / name for name in ("company_ingest.py", "company_ingest_v2.py", "company_ingest_v3.py")]
    paths += [BASE_DIR / "worker" / name for name in ("generate_embeddings.py", "sync_supabase.py")]
    return {p.relative_to(PROJECT_ROOT).as_posix(): sha256_file(p) for p in sorted(paths)}


def validate_vectors(embeddings: np.ndarray) -> dict:
    if embeddings.shape != EXPECTED_SHAPE:
        raise ValueError(f"Unexpected shape: {embeddings.shape}")
    if embeddings.dtype != np.float32:
        raise ValueError(f"Unexpected dtype: {embeddings.dtype}")
    nan_count, inf_count = int(np.isnan(embeddings).sum()), int(np.isinf(embeddings).sum())
    if nan_count or inf_count:
        raise ValueError("Non-finite embeddings")
    norms = np.linalg.norm(embeddings.astype(np.float64), axis=1)
    if not np.allclose(norms, 1.0, atol=1e-6, rtol=0):
        raise ValueError("Embeddings are not L2-normalized within 1e-6")
    return {"embedding_shape": list(embeddings.shape), "embedding_dtype": str(embeddings.dtype),
            "nan_count": nan_count, "inf_count": inf_count,
            "norm_min": float(norms.min()), "norm_mean": float(norms.mean()), "norm_max": float(norms.max()),
            "norm_max_abs_deviation": float(np.max(np.abs(norms - 1.0))), "norm_atol": 1e-6}


def row_map(chunks: list[dict]) -> list[dict]:
    return [{"embedding_row": i, "doc_id": c["doc_id"], "chunk_index": c["chunk_index"],
             "page": c["page"], "chunk_type": c["chunk_type"], "article_title": c.get("article_title"),
             "table_id": c.get("table_id"), "text_sha256": hashlib.sha256(c["text"].encode()).hexdigest()}
            for i, c in enumerate(chunks)]


def verify_saved(chunks: list[dict], embedding_path: Path, metadata_path: Path, *, label: str = LABEL) -> dict:
    with embedding_path.open("rb") as file:
        embeddings = np.asarray(pickle.load(file))
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    stats = validate_vectors(embeddings)
    if metadata.get("label") != label or metadata.get("embedding_model") != MODEL or metadata.get("embedding_dimensions") != 1024:
        raise ValueError("Wrong saved model metadata")
    if metadata.get("chunking_version") != "v3" or metadata.get("normalize_embeddings") is not True:
        raise ValueError("Wrong chunking/normalization metadata")
    if metadata.get("document_count") != 18 or metadata.get("chunk_count") != len(chunks) or len(embeddings) != len(chunks):
        raise ValueError("Saved count mismatch")
    if any(metadata.get(k) != v for k, v in stats.items()):
        raise ValueError("Vector statistics differ from metadata")
    if metadata["chunk_fingerprint_sha256"] != chunk_fingerprint(chunks):
        raise ValueError("Saved fingerprint mismatch")
    if metadata["source_chunk_sha256"] != sha256_file(CHUNK_FILE) or metadata["source_chunk_file"] != CHUNK_FILE.relative_to(PROJECT_ROOT).as_posix():
        raise ValueError("Source chunk file changed")
    if metadata["embedding_sha256"] != sha256_file(embedding_path) or metadata["embedding_file"] != embedding_path.name:
        raise ValueError("Embedding file hash/name mismatch")
    if metadata["row_mapping"] != row_map(chunks):
        raise ValueError("Saved row correspondence changed")
    if metadata["protected_artifact_sha256"] != protected_hashes():
        raise ValueError("Protected files changed")
    if Path(MODEL).is_dir():
        model_hashes = {p.relative_to(MODEL).as_posix(): sha256_file(p)
                        for p in sorted(Path(MODEL).rglob("*")) if p.is_file()}
        if metadata.get("model_snapshot_sha256") != model_hashes:
            raise ValueError("Local checkpoint changed since embedding generation")
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--label", default=LABEL, help="New artifact label; existing output is never overwritten")
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error("batch-size must be positive")
    if not args.label.replace("-", "").replace("_", "").isalnum():
        parser.error("label must contain only letters, numbers, '-' or '_'")
    chunks, preflight = load_chunks()
    embedding_path = OUTPUT_DIR / f"{args.label}_embeddings.pkl"
    metadata_path = OUTPUT_DIR / f"{args.label}_metadata.json"
    if args.verify_only:
        print(json.dumps(verify_saved(chunks, embedding_path, metadata_path, label=args.label), indent=2))
        return
    before = protected_hashes()
    print(json.dumps({"preflight": preflight, "protected_file_count": len(before)}, ensure_ascii=False), flush=True)
    if args.validate_only:
        return
    if embedding_path.exists() or metadata_path.exists():
        raise FileExistsError("Refusing to overwrite existing v3 artifacts; use --verify-only")
    # Use the backend's configured checkpoint; never fall back after load errors.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    import torch
    import sentence_transformers
    import transformers
    from huggingface_hub import try_to_load_from_cache

    if Path(MODEL).is_dir():
        snapshot = Path(MODEL)
        revision = None
    else:
        config_path = try_to_load_from_cache(MODEL, "config.json")
        if not isinstance(config_path, str):
            raise FileNotFoundError(f"{MODEL} must already exist in the local Hugging Face cache")
        snapshot = Path(config_path).parent
        revision = snapshot.name
    modules = json.loads((snapshot / "modules.json").read_text(encoding="utf-8"))
    if not any(m["type"].endswith(".Pooling") for m in modules):
        raise ValueError("Incomplete SentenceTransformer snapshot; fallback prohibited")
    model_files = {p.relative_to(snapshot).as_posix(): sha256_file(p) for p in sorted(snapshot.rglob("*")) if p.is_file()}
    threads = min(8, os.cpu_count() or 1)
    torch.set_num_threads(threads)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(json.dumps({"loading_model": MODEL, "revision": snapshot.name, "device": device, "offline": True}), flush=True)
    model = load_embedding_model(revision=revision, device=device, local_files_only=True)
    if model.get_sentence_embedding_dimension() != 1024:
        raise ValueError("Loaded model dimension is not 1024")
    print(json.dumps({"embedding_model": MODEL, "embedding_dimensions": 1024,
                      "max_seq_length": model.max_seq_length}), flush=True)
    texts = [c["text"] for c in chunks]
    token_lengths = [len(ids) for ids in model.tokenizer(texts, truncation=False, padding=False)["input_ids"]]
    if max(token_lengths) > model.max_seq_length:
        raise ValueError("Input would be truncated by the configured model")
    # Exactly one encode call over text, in source list order. content is not read.
    embeddings = np.asarray(model.encode(texts, normalize_embeddings=True, convert_to_numpy=True,
                                         show_progress_bar=True, batch_size=args.batch_size, precision="float32"))
    if embeddings.dtype == np.float16:
        embeddings = embeddings.astype(np.float32)
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        if np.any(norms == 0) or not np.isfinite(norms).all():
            raise ValueError("Cannot L2-normalize embeddings with zero or non-finite norms")
        embeddings = embeddings / norms
    stats = validate_vectors(embeddings)
    if before != protected_hashes():
        raise ValueError("Protected artifacts changed during encoding; refusing output")
    payload = pickle.dumps(embeddings, protocol=pickle.HIGHEST_PROTOCOL)
    mapping = row_map(chunks)
    metadata = {"label": args.label, "embedding_model": MODEL, "embedding_dimensions": 1024,
                "normalize_embeddings": True, "document_count": 18, "chunk_count": 115,
                "chunking_version": "v3", "source_chunk_file": CHUNK_FILE.relative_to(PROJECT_ROOT).as_posix(),
                "source_chunk_sha256": sha256_file(CHUNK_FILE), "text_field": "text",
                "chunk_fingerprint_sha256": preflight["chunk_fingerprint_sha256"],
                "embedding_file": embedding_path.name, "embedding_sha256": hashlib.sha256(payload).hexdigest(),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "model_revision": snapshot.name, "model_snapshot_sha256": model_files,
                "device": device, "torch_threads": threads, "batch_size": args.batch_size,
                "token_length_max": max(token_lengths), "model_max_seq_length": model.max_seq_length,
                "versions": {"torch": torch.__version__, "sentence_transformers": sentence_transformers.__version__,
                             "transformers": transformers.__version__, "numpy": np.__version__},
                "row_mapping": mapping, "protected_artifact_sha256": before,
                "protected_artifacts_unchanged": True, **stats}
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with embedding_path.open("xb") as file:
        file.write(payload)
    with metadata_path.open("x", encoding="utf-8") as file:
        json.dump(metadata, file, ensure_ascii=False, indent=2)
        file.write("\n")
    verified = verify_saved(chunks, embedding_path, metadata_path, label=args.label)
    if not np.array_equal(embeddings, pickle.loads(embedding_path.read_bytes())):
        raise ValueError("Saved embedding values changed")
    samples = [r for r in mapping if (r["doc_id"] == "HR-001" and r["article_title"] == "제6조 (근로시간)")
               or r["table_id"] == "GA-001:p1:t1"]
    print(json.dumps({"result": "PASS", "embedding_file": str(embedding_path), "metadata_file": str(metadata_path),
                      "fingerprint": preflight["chunk_fingerprint_sha256"], "stats": verified,
                      "samples": samples, "protected_file_count": len(before)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
