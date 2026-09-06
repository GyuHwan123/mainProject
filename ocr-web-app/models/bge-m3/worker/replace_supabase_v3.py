"""Explicitly prepared/approved replacement of the 18 company RAG documents' chunks.

No embedding generation or schema changes. REST requests cannot share a SQL
transaction: retain full originals and compensate on failure. Never delete by a
document filter alone: every deletion is also limited to exact recorded row IDs.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import pickle
import urllib.parse
import urllib.request
import uuid

import numpy as np
import generate_embeddings_v3 as v3

PROJECT = v3.PROJECT_ROOT
BACKUP_ROOT = v3.COMPANY_DIR / "db_backups_v3"


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def canonical_ids():
    tree = ast.parse((PROJECT / "backend/app/services/supabase_base.py").read_text(encoding="utf-8"))
    assignment = next(n for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "COMPANY_RAG_DOCUMENT_IDS" for t in n.targets))
    result = tuple(ast.literal_eval(assignment.value))
    if len(result) != 18 or len(set(result)) != 18:
        raise ValueError("Invalid canonical company ID list")
    return result


def vector(value):
    return np.fromstring(value.strip("[]"), sep=",", dtype=np.float64) if isinstance(value, str) else np.asarray(value, dtype=np.float64)


def read_env():
    result = {}
    for line in (PROJECT / ".env").read_text(encoding="utf-8-sig").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            result[key.strip()] = value.strip().strip('"').strip("'")
    return result


class API:
    def __init__(self):
        env = read_env()
        self.base = env["SUPABASE_URL"].rstrip("/") + "/rest/v1/"
        key = env["SUPABASE_SERVICE_ROLE_KEY"]
        self.headers = {"apikey": key}
        if key.startswith("eyJ"):
            self.headers["Authorization"] = "Bearer " + key

    def request(self, method, table="", params=None, payload=None):
        headers = dict(self.headers)
        if method != "GET":
            if table != "rag_chunks" or method not in ("POST", "DELETE"):
                raise ValueError("Writes are restricted to rag_chunks INSERT/DELETE")
            headers.update({"Content-Type": "application/json", "Prefer": "return=representation"})
        url = self.base + table + ("?" + urllib.parse.urlencode(params) if params else "")
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
        request = urllib.request.Request(url, headers=headers, method=method, data=data)
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.load(response)

    def rows(self, table, **filters):
        result = []
        while True:
            page = self.request("GET", table, {"select": "*", "order": "id.asc", "limit": "1000", "offset": str(len(result)), **filters})
            result.extend(page)
            if len(page) < 1000:
                return result


def runtime():
    with urllib.request.urlopen("http://127.0.0.1:8000/api/v1/chatbot/status", timeout=15) as response:
        result = json.load(response)
    if result.get("embedding_model") != v3.MODEL or result.get("embedding_dimensions") != 1024:
        raise ValueError("Runtime query model is not Base BGE-M3 / 1024")
    return {"embedding_model": result["embedding_model"], "embedding_dimensions": result["embedding_dimensions"]}


def local_inputs():
    chunks, validation = v3.load_chunks()
    embeddings_path = v3.OUTPUT_DIR / "baseline-v3_embeddings.pkl"
    metadata_path = v3.OUTPUT_DIR / "baseline-v3_metadata.json"
    v3.verify_saved(chunks, embeddings_path, metadata_path)
    with embeddings_path.open("rb") as file:
        embeddings = np.asarray(pickle.load(file))
    if {c["doc_id"] for c in chunks} != set(canonical_ids()):
        raise ValueError("V3 documents differ from COMPANY_RAG_DOCUMENT_IDS")
    return chunks, embeddings, validation


def state(api):
    documents = api.rows("rag_documents")
    company_docs = [d for d in documents if d.get("doc_id") in canonical_ids()]
    if len(company_docs) != 18 or {d["doc_id"] for d in company_docs} != set(canonical_ids()):
        raise ValueError("Company document mapping must be exactly 18")
    if any(d.get("deleted_at") for d in company_docs):
        raise ValueError("A company document is soft-deleted")
    mapping = {d["doc_id"]: d["id"] for d in company_docs}
    if len(set(mapping.values())) != 18:
        raise ValueError("Duplicate document UUID mapping")
    rows = api.rows("rag_chunks")
    selected = [r for r in rows if r["document_id"] in mapping.values()]
    other = [r for r in rows if r["document_id"] not in mapping.values()]
    return {"document_mapping": mapping, "company_documents": company_docs, "company_chunks": selected,
            "rag_documents_sha256": digest(documents), "other_chunks_sha256": digest(other),
            "other_chunks_count": len(other), "all_chunk_ids": [r["id"] for r in rows]}


def index_rows(snapshot):
    reverse = {value: key for key, value in snapshot["document_mapping"].items()}
    rows = snapshot["company_chunks"]
    index = {(reverse[r["document_id"]], int(r["chunk_index"])): r for r in rows}
    if len(index) != len(rows):
        raise ValueError("Duplicate company chunk keys")
    return index


def verify_old(snapshot):
    with (v3.COMPANY_DIR / "processed/company_chunks.pkl").open("rb") as file:
        chunks = pickle.load(file)
    with (v3.COMPANY_DIR / "embedding_variants/baseline_embeddings.pkl").open("rb") as file:
        embeddings = np.asarray(pickle.load(file))
    index = index_rows(snapshot)
    if len(index) != 37 or embeddings.shape != (37, 1024):
        raise ValueError("Expected the existing 37 Base chunks")
    if set(index) != {(c["doc_id"], c["chunk_index"]) for c in chunks}:
        raise ValueError("Old chunk mapping differs")
    for c, e in zip(chunks, embeddings):
        row = index[c["doc_id"], c["chunk_index"]]
        if row["content"] != c["text"] or row["page_number"] != c["page"]:
            raise ValueError("Old content/page differs from baseline source")
        if vector(row["embedding"]).shape != (1024,) or not np.allclose(vector(row["embedding"]), e, atol=1e-6, rtol=0):
            raise ValueError("Old DB embeddings are not baseline 37/37")


def payload_rows(chunks, embeddings, mapping, schema, new_ids=None):
    required = {"id", "document_id", "chunk_index", "page_number", "content", "embedding", "document_title", "section_path"}
    columns = set(schema["properties"])
    if not required <= columns or schema["properties"]["embedding"]["format"] != "public.vector(1024)":
        raise ValueError("Unsupported existing schema")
    if set(mapping) != {c["doc_id"] for c in chunks}:
        raise ValueError("Wrong document mapping")
    rows = []
    for i, (c, embedding) in enumerate(zip(chunks, embeddings)):
        row = {"id": new_ids[i] if new_ids else str(uuid.uuid4()), "document_id": mapping[c["doc_id"]],
               "chunk_index": c["chunk_index"], "page_number": c["page"], "content": c["text"],
               "embedding": embedding.astype(np.float32).tolist(), "document_title": c["document_title"],
               "section_title": c.get("section_title"), "section_path": c.get("section_path", []),
               "heading_level": c.get("heading_level"), "bbox": c.get("bbox")}
        rows.append({k: v for k, v in row.items() if k in columns})
    if len(rows) != 115 or len({r["id"] for r in rows}) != 115:
        raise ValueError("Invalid replacement payload")
    return rows


def verify_new(snapshot, chunks, embeddings, payload, before):
    if snapshot["document_mapping"] != before["document_mapping"]:
        raise ValueError("Document mapping changed")
    index = index_rows(snapshot)
    expected_keys = {(c["doc_id"], c["chunk_index"]) for c in chunks}
    if len(index) != 115 or set(index) != expected_keys:
        raise ValueError("Expected exactly the 115 V3 keys")
    diffs, cosines = [], []
    for c, embedding, planned in zip(chunks, embeddings, payload):
        row = index[c["doc_id"], c["chunk_index"]]
        for key, value in planned.items():
            if key != "embedding" and row.get(key) != value:
                raise ValueError(f"Stored field mismatch: {c['doc_id']}:{c['chunk_index']}:{key}")
        actual, expected = vector(row["embedding"]), embedding.astype(np.float64)
        if actual.shape != (1024,) or not np.isfinite(actual).all() or not np.allclose(actual, expected, atol=1e-6, rtol=0):
            raise ValueError("V3 embedding mismatch")
        diffs.append(float(np.max(np.abs(actual - expected))))
        cosines.append(float(np.dot(actual, expected) / (np.linalg.norm(actual) * np.linalg.norm(expected))))
    old_ids = {r["id"] for r in before["company_chunks"]}
    if old_ids.intersection(snapshot["all_chunk_ids"]):
        raise ValueError("Old chunk IDs remain")
    for key in ("rag_documents_sha256", "other_chunks_sha256", "other_chunks_count"):
        if snapshot[key] != before[key]:
            raise ValueError(f"Out-of-scope data changed: {key}")
    return {"documents": 18, "chunks": 115, "key_matches": 115, "content_matches": 115,
            "page_matches": 115, "embedding_matches_atol_1e_6_rtol_0": 115, "embedding_shape": [115, 1024],
            "max_abs_diff": max(diffs), "cosine_min": min(cosines), "cosine_avg": float(np.mean(cosines)),
            "cosine_max": max(cosines), "empty_content": sum(not r["content"].strip() for r in index.values()),
            "duplicate_keys": 0, "stale_keys": 0, "old_chunk_ids_remaining": 0, "company_orphans": 0,
            "rag_documents_unchanged": True, "other_user_chunks_unchanged": True,
            "stored_metadata_matches": True}


def delete_exact(api, row_ids, document_ids):
    if not row_ids or len(document_ids) != 18:
        raise ValueError("Unsafe deletion scope")
    return api.request("DELETE", "rag_chunks", {"id": "in.(" + ",".join(row_ids) + ")",
                       "document_id": "in.(" + ",".join(document_ids) + ")", "select": "id,document_id"})


def restore(api, before, payload):
    """Compensate only our exact new IDs, restoring originals including UUIDs."""
    current = state(api)
    old_by_id = {r["id"]: r for r in before["company_chunks"]}
    new_ids = {r["id"] for r in payload}
    current_ids = {r["id"] for r in current["company_chunks"]}
    if current_ids - set(old_by_id) - new_ids:
        raise RuntimeError("Concurrent unknown company rows detected; automatic restore stopped")
    remove = sorted(current_ids & new_ids)
    if remove:
        delete_exact(api, remove, list(before["document_mapping"].values()))
    survivors = state(api)["company_chunks"]
    if any(r != old_by_id[r["id"]] for r in survivors):
        raise RuntimeError("Original surviving rows changed; automatic restore stopped")
    present = {r["id"] for r in survivors}
    missing = [r for r in before["company_chunks"] if r["id"] not in present]
    if missing:
        api.request("POST", "rag_chunks", {"select": "id"}, missing)
    recovered = state(api)
    if digest(recovered) != digest(before):
        raise RuntimeError("Restore verification failed; retain backup for review")


def write_json(path, value):
    with path.open("x", encoding="utf-8") as file:
        json.dump(value, file, ensure_ascii=False, indent=2)
        file.write("\n")


def prepare(api, chunks, embeddings, validation):
    snapshot = state(api)
    verify_old(snapshot)
    query = runtime()
    schema = api.request("GET")["definitions"]["rag_chunks"]
    payload = payload_rows(chunks, embeddings, snapshot["document_mapping"], schema)
    if {r["id"] for r in payload}.intersection(snapshot["all_chunk_ids"]):
        raise ValueError("Planned UUID collision")
    folder = BACKUP_ROOT / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8])
    folder.mkdir(parents=True, exist_ok=False)
    backup_path = folder / "before_company_chunks_full.json"
    write_json(backup_path, snapshot)
    write_json(folder / "planned_v3_rows.json", payload)
    report = {"preflight": "PASS", "validation": validation, "query_model": query,
              "existing_documents": 18, "existing_chunks": 37, "old_baseline_matches": 37,
              "delete_count": 37, "insert_count": 115, "document_mapping": snapshot["document_mapping"],
              "schema": schema, "backup_sha256": v3.sha256_file(backup_path),
              "payload_sha256": v3.sha256_file(folder / "planned_v3_rows.json"),
              "local_protected_hashes": v3.protected_hashes(),
              "local_v3_embedding_sha256": v3.sha256_file(v3.OUTPUT_DIR / "baseline-v3_embeddings.pkl"),
              "unstored_local_metadata": ["chunk_type", "start_page", "end_page", "article_title", "table_id", "source_spans"]}
    write_json(folder / "preflight.json", report)
    print(json.dumps({"preflight": "PASS", "run_dir": str(folder), "backup": str(backup_path),
                      "backup_sha256": report["backup_sha256"], "mapping": snapshot["document_mapping"],
                      "old_chunks": 37, "new_chunks": 115, "old_baseline_matches": 37,
                      "stored_columns": list(payload[0]), "query": query}, ensure_ascii=False, indent=2))


def apply_or_verify(api, folder, chunks, embeddings, verify_only):
    folder = folder.resolve()
    if not folder.is_relative_to(BACKUP_ROOT.resolve()):
        raise ValueError("Run directory must be under db_backups_v3")
    report = json.loads((folder / "preflight.json").read_text(encoding="utf-8"))
    if v3.sha256_file(folder / "before_company_chunks_full.json") != report["backup_sha256"] or v3.sha256_file(folder / "planned_v3_rows.json") != report["payload_sha256"]:
        raise ValueError("Backup/payload integrity check failed")
    before = json.loads((folder / "before_company_chunks_full.json").read_text(encoding="utf-8"))
    payload = json.loads((folder / "planned_v3_rows.json").read_text(encoding="utf-8"))
    expected = payload_rows(chunks, embeddings, before["document_mapping"], report["schema"], [r["id"] for r in payload])
    if payload != expected or report["local_protected_hashes"] != v3.protected_hashes():
        raise ValueError("Prepared payload/input no longer matches validated source")
    runtime()
    if not verify_only:
        if (folder / "applied.json").exists():
            raise ValueError("Already applied; use verify-only")
        current = state(api)
        if digest(current) != digest(before):
            raise ValueError("DB changed since preparation; no writes performed")
        verify_old(current)
        if api.request("GET")["definitions"]["rag_chunks"] != report["schema"]:
            raise ValueError("DB schema changed since preparation")
        try:
            removed = delete_exact(api, [r["id"] for r in before["company_chunks"]], list(before["document_mapping"].values()))
            if {r["id"] for r in removed} != {r["id"] for r in before["company_chunks"]}:
                raise ValueError("Unexpected DELETE row set")
            inserted = api.request("POST", "rag_chunks", {"select": "id"}, payload)
            if {r["id"] for r in inserted} != {r["id"] for r in payload}:
                raise ValueError("Unexpected INSERT row set")
            result = verify_new(state(api), chunks, embeddings, payload, before)
        except Exception:
            restore(api, before, payload)
            print("APPLY FAILED: full original 37 rows restored and verified", flush=True)
            raise
    after = state(api)
    result = verify_new(after, chunks, embeddings, payload, before)
    if report["local_protected_hashes"] != v3.protected_hashes():
        raise ValueError("Protected local files changed")
    index = index_rows(after)
    samples = []
    for doc_id, chunk_index in [("HR-001", 3), ("HR-001", 4), ("HR-001", 0), ("GA-001", 1), ("GA-001", 2), ("GA-001", 3)]:
        row = index[doc_id, chunk_index]
        samples.append({"doc_id": doc_id, **{k: row[k] for k in ("id", "chunk_index", "page_number", "content", "section_title")}})
    output = {"result": "PASS", **result, "query": runtime(), "samples": samples,
              "backup": str(folder / "before_company_chunks_full.json"), "backup_sha256": report["backup_sha256"]}
    if not verify_only:
        write_json(folder / "applied.json", output)
    print(json.dumps(output, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true", help="GET-only preflight and full local backup")
    mode.add_argument("--apply-run", type=Path, help="Approved DELETE/INSERT; restore originals on failure")
    mode.add_argument("--verify-run", type=Path, help="GET-only verification of an applied run")
    args = parser.parse_args()
    chunks, embeddings, validation = local_inputs()
    api = API()
    if args.prepare:
        prepare(api, chunks, embeddings, validation)
    else:
        apply_or_verify(api, args.verify_run or args.apply_run, chunks, embeddings, bool(args.verify_run))


if __name__ == "__main__":
    main()
