"""Search-only smoke test against the running authenticated local RAG endpoint.

Requires the operator's explicitly supplied login email. The short-lived local
test token is kept in memory and never printed or saved. No DB writes or model
generation; only the normal search endpoint computes query/evidence embeddings.
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import urllib.request

from jose import jwt

import replace_supabase_v3 as replacement


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True, help="Operator-authorized existing login email")
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    if not run_dir.is_relative_to(replacement.BACKUP_ROOT.resolve()):
        raise ValueError("Invalid run directory")
    applied = json.loads((run_dir / "applied.json").read_text(encoding="utf-8"))
    if applied.get("result") != "PASS" or applied.get("embedding_matches_atol_1e_6_rtol_0") != 115:
        raise ValueError("V3 DB verification must pass before search")
    query_model = replacement.runtime()
    settings = replacement.read_env()
    token = jwt.encode({"sub": args.email.strip().lower(),
                        "exp": datetime.now(timezone.utc) + timedelta(minutes=10)},
                       settings["SECRET_KEY"], algorithm=settings.get("ALGORITHM", "HS256"))
    results = []
    for question, expected_doc, expected_indices in [
        ("근무시간 알려줘", "HR-001", {3}),
        ("출장 숙박비 한도 알려줘", "GA-001", {2, 3}),
    ]:
        request = urllib.request.Request("http://127.0.0.1:8000/api/v1/rag/search",
                  headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
                  data=json.dumps({"query": question, "limit": 10}).encode(), method="POST")
        with urllib.request.urlopen(request, timeout=300) as response:
            rows = json.load(response)
        # Use verified UUIDs to avoid depending on optional API doc_id/index fields.
        plan = json.loads((run_dir / "planned_v3_rows.json").read_text(encoding="utf-8"))
        mapping = json.loads((run_dir / "preflight.json").read_text(encoding="utf-8"))["document_mapping"]
        by_id = {r["id"]: r for r in plan}
        reverse = {v: k for k, v in mapping.items()}
        summary = []
        for rank, row in enumerate(rows, 1):
            chunk_id = row.get("id") or row.get("chunk_id")
            planned = by_id.get(chunk_id, {})
            doc_id = reverse.get(planned.get("document_id") or row.get("document_id")) or row.get("doc_id")
            chunk_index = planned.get("chunk_index", row.get("chunk_index"))
            summary.append({"rank": rank, "doc_id": doc_id, "chunk_index": chunk_index,
                            "content": row.get("content"), "similarity": row.get("similarity"),
                            "rerank_score": row.get("rerank_score")})
        hits = [r["rank"] for r in summary if r["doc_id"] == expected_doc and r["chunk_index"] in expected_indices]
        results.append({"query": question, "result_count": len(rows), "nonempty": bool(rows),
                        "expected_ranks": hits, "passed": bool(rows) and bool(hits), "results": summary})
        print(json.dumps(results[-1], ensure_ascii=False), flush=True)
    output = {"query_model": query_model, "endpoint": "/api/v1/rag/search", "limit": 10,
              "passed": all(r["passed"] for r in results), "tests": results}
    replacement.write_json(run_dir / "smoke_test.json", output)
    print(json.dumps({"smoke_passed": output["passed"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
