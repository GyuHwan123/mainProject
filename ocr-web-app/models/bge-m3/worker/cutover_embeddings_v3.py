"""Back up and replace ONLY embeddings of the frozen 115 company chunks.

Default: read/validate and create a backup, no DB writes. --apply enables writes.
Stop backend/ingestion writers for the entire cutover or rollback operation.
REST is not transactional: failures trigger compensation; a durable backup also
supports --rollback after interruption. No INSERT, DELETE or schema operations.
"""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import pickle
import urllib.parse
import urllib.request
import uuid

import numpy as np
from dotenv import dotenv_values

PROJECT = Path(__file__).resolve().parents[3]
COMPANY = PROJECT / "models/bge-m3/data/company_documents"
VARIANTS = COMPANY / "embedding_variants_v3"


def file_hash(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def canonical_ids():
    tree = ast.parse((PROJECT / "backend/app/services/supabase_base.py").read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "COMPANY_RAG_DOCUMENT_IDS" for t in n.targets))
    ids = tuple(ast.literal_eval(node.value))
    if len(ids) != 18 or len(set(ids)) != 18:
        raise ValueError("Expected 18 unique canonical company documents")
    return ids


def vector(value):
    result = np.asarray(json.loads(value) if isinstance(value, str) else value, dtype=np.float32)
    if result.shape != (1024,) or not np.isfinite(result).all():
        raise ValueError("Expected a finite 1024-dimensional embedding")
    return result


def same_vector(left, right):
    return np.array_equal(vector(left), vector(right))


def unchanged_fields(row):
    return {key: value for key, value in row.items() if key != "embedding"}


class API:
    def __init__(self):
        # Match backend precedence without loading/validating a model, so a
        # rollback remains available even if the checkpoint has been removed.
        env = {**dotenv_values(PROJECT / ".env"), **dotenv_values(PROJECT / "backend/.env"), **os.environ}
        url = env.get("SUPABASE_URL") or ""
        key = env.get("SUPABASE_SERVICE_ROLE_KEY") or ""
        self.base = url.rstrip("/") + "/rest/v1/"
        if not url or not key:
            raise ValueError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required")
        self.headers = {"apikey": key}
        if key.startswith("eyJ"):
            self.headers["Authorization"] = "Bearer " + key

    def request(self, method, table, params, payload=None):
        if method not in {"GET", "PATCH"} or table not in {"rag_documents", "rag_chunks"}:
            raise ValueError("Unsupported request")
        if method == "PATCH" and (table != "rag_chunks" or set(payload or {}) != {"embedding"}
                                   or not all(key in params for key in ("id", "document_id", "chunk_index"))):
            raise ValueError("Only an exact chunk's embedding may be patched")
        headers = {**self.headers, "Content-Type": "application/json", "Prefer": "return=representation"}
        data = None if payload is None else json.dumps(payload, allow_nan=False).encode()
        request = urllib.request.Request(self.base + table + "?" + urllib.parse.urlencode(params),
                                         data=data, headers=headers, method=method)
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.load(response)

    def rows(self, table, **filters):
        result = []
        while True:
            page = self.request("GET", table, {"select": "*", "order": "id.asc", "limit": 100,
                                               "offset": len(result), **filters})
            if not page:
                return result
            result.extend(page)


def snapshot(api):
    ids = canonical_ids()
    documents = api.rows("rag_documents", doc_id="in.(" + ",".join(ids) + ")")
    if len(documents) != 18 or {d["doc_id"] for d in documents} != set(ids):
        raise ValueError("Company document mapping is not exactly 18:18")
    if any(d.get("deleted_at") for d in documents) or len({d["id"] for d in documents}) != 18:
        raise ValueError("Deleted or duplicate company document")
    rows = api.rows("rag_chunks", document_id="in.(" + ",".join(d["id"] for d in documents) + ")")
    reverse = {d["id"]: d["doc_id"] for d in documents}
    keys = [(reverse[r["document_id"]], r["chunk_index"]) for r in rows]
    if len(rows) != 115 or len(set(keys)) != 115 or len({r["id"] for r in rows}) != 115:
        raise ValueError("Expected exactly 115 unique company chunks")
    for row in rows:
        vector(row["embedding"])
    return {"documents": documents, "rows": rows}


def artifacts():
    metadata_path = VARIANTS / "finetuned-v3_metadata.json"
    embedding_path = VARIANTS / "finetuned-v3_embeddings.pkl"
    chunk_path = COMPANY / "processed_v3/company_chunks_v3.pkl"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    expected = {"label": "finetuned-v3", "embedding_dimensions": 1024,
                "document_count": 18, "chunk_count": 115, "chunking_version": "v3",
                "normalize_embeddings": True, "embedding_dtype": "float32"}
    if any(metadata.get(k) != v for k, v in expected.items()):
        raise ValueError("Wrong fine-tuned artifact metadata")
    if metadata["embedding_file"] != embedding_path.name or metadata["embedding_sha256"] != file_hash(embedding_path):
        raise ValueError("Embedding artifact hash/name mismatch")
    if metadata["source_chunk_file"] != chunk_path.relative_to(PROJECT).as_posix() or metadata["source_chunk_sha256"] != file_hash(chunk_path):
        raise ValueError("Frozen chunk source changed")
    checkpoint = Path(metadata["embedding_model"])
    hashes = {p.relative_to(checkpoint).as_posix(): file_hash(p)
              for p in sorted(checkpoint.rglob("*")) if p.is_file()}
    if not hashes or hashes != metadata["model_snapshot_sha256"]:
        raise ValueError("Checkpoint differs from the model used to generate these vectors")
    with embedding_path.open("rb") as stream:
        embeddings = np.asarray(pickle.load(stream))
    with chunk_path.open("rb") as stream:
        chunks = pickle.load(stream)
    if embeddings.shape != (115, 1024) or embeddings.dtype != np.float32 or not np.isfinite(embeddings).all():
        raise ValueError("Expected finite float32 (115, 1024) embeddings")
    if not np.allclose(np.linalg.norm(embeddings.astype(np.float64), axis=1), 1.0, atol=1e-6, rtol=0):
        raise ValueError("Non-normalized fine-tuned vectors")
    stable = [{"doc_id": c["doc_id"], "chunk_index": int(c["chunk_index"]),
               "page": int(c["page"]), "text": c["text"]} for c in chunks]
    fingerprint = hashlib.sha256(json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    mapping = [{"embedding_row": i, "doc_id": c["doc_id"], "chunk_index": c["chunk_index"],
                "page": c["page"], "chunk_type": c["chunk_type"], "article_title": c.get("article_title"),
                "table_id": c.get("table_id"), "text_sha256": hashlib.sha256(c["text"].encode()).hexdigest()}
               for i, c in enumerate(chunks)]
    if len(chunks) != 115 or metadata["row_mapping"] != mapping or metadata["chunk_fingerprint_sha256"] != fingerprint:
        raise ValueError("Frozen chunk row mapping/fingerprint mismatch")
    return metadata, embeddings, file_hash(metadata_path)


def prepare(api):
    metadata, embeddings, metadata_hash = artifacts()
    before = snapshot(api)
    reverse = {d["id"]: d["doc_id"] for d in before["documents"]}
    indexed = {(reverse[r["document_id"]], r["chunk_index"]): r for r in before["rows"]}
    mapping = metadata["row_mapping"]
    keys = [(m["doc_id"], m["chunk_index"]) for m in mapping]
    if len(set(keys)) != 115 or set(keys) != set(indexed) or {m["doc_id"] for m in mapping} != set(canonical_ids()):
        raise ValueError("Artifact and DB chunks are not a 115:115 bijection")
    targets = {}
    for m in mapping:
        row = indexed[m["doc_id"], m["chunk_index"]]
        if hashlib.sha256(row["content"].encode()).hexdigest() != m["text_sha256"] or row["page_number"] != m["page"]:
            raise ValueError(f"Text/page mismatch: {m['doc_id']} chunk {m['chunk_index']}")
        targets[row["id"]] = embeddings[m["embedding_row"]].tolist()
    return {"format": "embedding-only-cutover-v1", "endpoint": api.base,
            "created_at": datetime.now(timezone.utc).isoformat(), "before": before,
            "targets": targets, "embedding_model": metadata["embedding_model"],
            "metadata_sha256": metadata_hash, "row_mapping": mapping}


def save_backup(plan):
    folder = COMPANY / "embedding_cutover_backups" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8])
    folder.mkdir(parents=True, exist_ok=False)
    path = folder / "backup.json"
    data = json.dumps(plan, ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8")
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    with path.with_suffix(".sha256").open("x", encoding="ascii") as stream:
        stream.write(hashlib.sha256(data).hexdigest())
        stream.flush()
        os.fsync(stream.fileno())
    print(f"Backup: {path}", flush=True)
    return path


def check_snapshot(api, plan, *, restored=False, mixed=False):
    current = snapshot(api)
    before = plan["before"]
    if current["documents"] != before["documents"]:
        raise ValueError("Company document metadata changed; refusing writes")
    indexed = {r["id"]: r for r in current["rows"]}
    if set(indexed) != {r["id"] for r in before["rows"]} or set(plan["targets"]) != set(indexed):
        raise ValueError("Chunk IDs changed; refusing writes")
    for old in before["rows"]:
        row = indexed[old["id"]]
        if unchanged_fields(row) != unchanged_fields(old):
            raise ValueError(f"Non-embedding fields changed: {old['id']}")
        expected = old["embedding"] if restored else plan["targets"][old["id"]]
        if not same_vector(row["embedding"], expected):
            if not mixed or not same_vector(row["embedding"], old["embedding"]):
                raise ValueError(f"Unexpected embedding (possible concurrent writer): {old['id']}")
    return indexed


def patch(api, old, expected, target):
    filters = {"id": "eq." + old["id"], "document_id": "eq." + old["document_id"],
               "chunk_index": "eq." + str(old["chunk_index"])}
    current = api.rows("rag_chunks", **filters)
    if len(current) != 1 or unchanged_fields(current[0]) != unchanged_fields(old) or not same_vector(current[0]["embedding"], expected):
        raise ValueError(f"Row changed before PATCH: {old['id']}")
    if current[0].get("updated_at") is not None:
        filters["updated_at"] = "eq." + current[0]["updated_at"]
    result = api.request("PATCH", "rag_chunks", filters, {"embedding": vector(target).tolist()})
    if len(result) != 1 or unchanged_fields(result[0]) != unchanged_fields(old) or not same_vector(result[0]["embedding"], target):
        raise ValueError(f"PATCH verification failed: {old['id']}")


def rollback(api, plan):
    # Check all 115 before restoring any. Never overwrite an unknown third vector.
    current = check_snapshot(api, plan, mixed=True)
    failures = []
    for old in reversed(plan["before"]["rows"]):
        if same_vector(current[old["id"]]["embedding"], old["embedding"]):
            continue
        try:
            patch(api, old, plan["targets"][old["id"]], old["embedding"])
        except Exception as exc:
            failures.append(f"{old['id']}: {type(exc).__name__}")
    check_snapshot(api, plan, restored=True)
    if failures:
        print("Rollback requests had errors, but final read confirms all originals restored.", flush=True)
    print("ROLLBACK PASS: 115 original embeddings and all other fields preserved.", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--rollback", type=Path, metavar="BACKUP_JSON")
    args = parser.parse_args()
    api = API()
    if args.rollback:
        path = args.rollback.resolve()
        if file_hash(path) != path.with_suffix(".sha256").read_text(encoding="ascii").strip():
            raise ValueError("Backup checksum mismatch")
        plan = json.loads(path.read_text(encoding="utf-8"))
        if plan.get("format") != "embedding-only-cutover-v1" or plan["endpoint"] != api.base:
            raise ValueError("Wrong backup format or Supabase endpoint")
        rollback(api, plan)
        return
    plan = prepare(api)
    path = save_backup(plan)
    if not args.apply:
        print("PREPARED: 115 mappings verified; backup saved; no DB writes.", flush=True)
        return
    check_snapshot(api, plan, restored=True)
    try:
        for old in plan["before"]["rows"]:
            patch(api, old, old["embedding"], plan["targets"][old["id"]])
        check_snapshot(api, plan)
    except BaseException:
        print(f"Cutover interrupted; attempting rollback. Durable backup: {path}", flush=True)
        try:
            rollback(api, plan)
        except BaseException as rollback_error:
            print(f"Rollback incomplete ({type(rollback_error).__name__}); keep backend stopped and rerun --rollback {path}", flush=True)
        raise
    print(f"CUTOVER PASS: 115 embeddings only; model={plan['embedding_model']}; dimension=1024", flush=True)


if __name__ == "__main__":
    main()
