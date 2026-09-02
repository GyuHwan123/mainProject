from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import re
import sys
from pathlib import Path
from typing import Any

# backend/scripts/evaluate_rag_gate_only.py 기준
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

rag = importlib.import_module("app.services.rag_service")


def compact(value: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", str(value or "").lower())


def candidate_matches_expected(candidate: dict[str, Any], case: dict[str, Any]) -> bool:
    expected_values = [
        *case.get("expected_documents", []),
        *case.get("expected_document_titles", []),
    ]
    expected = [compact(v) for v in expected_values if compact(v)]

    searchable_values: list[str] = []
    for key in (
        "source", "file_name", "title", "document_title", "document_code",
        "document_id", "rag_document_id", "content"
    ):
        value = candidate.get(key)
        if value is not None:
            searchable_values.append(str(value))

    searchable = [compact(v) for v in searchable_values if compact(v)]
    return any(
        exp in got or got in exp
        for exp in expected
        for got in searchable
        if len(exp) >= 3 and len(got) >= 3
    )


def candidate_label(candidate: dict[str, Any]) -> str:
    for key in ("source", "file_name", "document_title", "title", "document_code", "document_id"):
        value = candidate.get(key)
        if value:
            return str(value)
    return "(unknown)"


async def retrieve_top4(
    user_email: str,
    query: str,
    *,
    user_role: str,
    subscription_tier: str,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[list[float]]]:
    # 현재 rag_service.search()의 Gate 직전 경로를 그대로 재현:
    # BGE -> search_rag_chunks -> lexical boost -> Reranker -> Top-4
    facets = rag._extract_evidence_facets(query)
    strong_subjects = facets["strong_subjects"]
    focus_subject = strong_subjects[0] if strong_subjects else ""

    query_texts = [query, facets["query"], *([focus_subject] if focus_subject else [])]
    query_vectors, _ = await rag._embed_texts_cached(query_texts)

    embedding = query_vectors[0]
    facet_vectors = query_vectors[1:]

    candidates = rag.supabase_service.search_rag_chunks(
        user_email,
        embedding,
        None,
        6,
        include_company_documents=rag.can_access_company_rag(user_role, subscription_tier),
    )

    compact_query = "".join(query.lower().split())
    requested_sections = [
        keywords
        for name, keywords in rag.SECTION_KEYWORDS.items()
        if name in compact_query
    ]
    query_terms = {
        token
        for token in query.lower().replace("?", " ").replace(".", " ").split()
        if len(token) >= 2
        and token not in {
            "어떻게", "알려줘", "알려주세요", "무엇",
            "뭐야", "지원자", "지원자의",
        }
    }

    for row in candidates:
        content = str(row.get("content", "")).lower()
        section_hits = sum(
            1
            for keywords in requested_sections
            for keyword in keywords
            if keyword in content
        )
        term_hits = sum(
            1 for term in query_terms
            if term.rstrip("은는이가을를의") in content
        )
        lexical_boost = min(0.45, section_hits * 0.07 + term_hits * 0.04)
        row["vector_similarity"] = float(row.get("similarity") or 0)
        row["similarity"] = min(
            1.0,
            row["vector_similarity"] + lexical_boost,
        )

    candidates.sort(
        key=lambda row: float(row.get("similarity") or 0),
        reverse=True,
    )
    candidates = await rag.rerank_candidates(query, candidates)
    candidates = candidates[:4]

    return candidates, facets, facet_vectors


async def main(args: argparse.Namespace) -> None:
    gt_path = Path(args.ground_truth)
    with gt_path.open("r", encoding="utf-8") as f:
        dataset = json.load(f)

    cases = dataset["cases"]

    counts = {
        "NORMAL": 0,
        "RETRIEVAL_FAILURE": 0,
        "FALSE_REJECTION": 0,
        "FALSE_ACCEPTANCE": 0,
        "ERROR": 0,
    }
    failures: list[dict[str, Any]] = []

    print("=== RAG Gate-only 50문항 검증 ===", flush=True)
    print("BGE -> Reranker -> Top-4 -> Facet-Evidence Gate", flush=True)
    print("LLM 호출 없음 / 답변 생성 없음 / Faithfulness 없음\n", flush=True)

    for case in cases:
        qid = case["question_id"]
        question = case["question"]
        answerable = bool(case["answerable"])

        try:
            top4, facets, facet_vectors = await retrieve_top4(
                args.user_email,
                question,
                user_role=args.user_role,
                subscription_tier=args.subscription_tier,
            )

            gate_accept = await rag._has_facet_evidence(
                question,
                top4,
                facets=facets,
                facet_vectors=facet_vectors,
            )

            retrieval_hit = (
                any(candidate_matches_expected(candidate, case) for candidate in top4)
                if answerable
                else None
            )

            if answerable:
                if not retrieval_hit:
                    status = "RETRIEVAL_FAILURE"
                elif not gate_accept:
                    status = "FALSE_REJECTION"
                else:
                    status = "NORMAL"
            else:
                status = "FALSE_ACCEPTANCE" if gate_accept else "NORMAL"

            counts[status] += 1
            verdict = "Accept" if gate_accept else "Reject"
            marker = "" if status == "NORMAL" else f"  <-- {status}"
            print(f"{qid} {verdict}{marker}", flush=True)

            if status != "NORMAL":
                failures.append({
                    "qid": qid,
                    "status": status,
                    "question": question,
                    "expected": case.get("expected_documents", []),
                    "top4": top4,
                })

        except Exception as exc:
            counts["ERROR"] += 1
            print(
                f"{qid} ERROR  <-- {type(exc).__name__}: {exc}",
                flush=True,
            )
            failures.append({
                "qid": qid,
                "status": "ERROR",
                "question": question,
                "error": f"{type(exc).__name__}: {exc}",
            })

    print("\n=== SUMMARY ===", flush=True)
    print(f"Total: {len(cases)}", flush=True)
    print(f"Normal: {counts['NORMAL']}", flush=True)
    print(f"Retrieval failure: {counts['RETRIEVAL_FAILURE']}", flush=True)
    print(f"False Rejection: {counts['FALSE_REJECTION']}", flush=True)
    print(f"False Acceptance: {counts['FALSE_ACCEPTANCE']}", flush=True)
    print(f"Error: {counts['ERROR']}", flush=True)

    if failures:
        print("\n=== FAILURES ONLY ===", flush=True)
        for item in failures:
            print(f"\n[{item['qid']}] {item['status']}", flush=True)
            print(item["question"], flush=True)

            if item["status"] == "ERROR":
                print(item["error"], flush=True)
                continue

            print(
                "Expected: " + (", ".join(item["expected"]) if item["expected"] else "(none)"),
                flush=True,
            )

            for i, candidate in enumerate(item["top4"], 1):
                rerank_score = candidate.get("rerank_score")
                vector_similarity = candidate.get("vector_similarity")

                rerank_text = (
                    f"{float(rerank_score):.6f}"
                    if rerank_score is not None
                    else "-"
                )
                vector_text = (
                    f"{float(vector_similarity):.6f}"
                    if vector_similarity is not None
                    else "-"
                )

                print(
                    f"  Top{i}: {candidate_label(candidate)} "
                    f"| rerank={rerank_text} "
                    f"| vector={vector_text}",
                    flush=True,
                )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="BGE -> Reranker -> Top-4 -> Facet-Evidence Gate only evaluator"
    )
    parser.add_argument("ground_truth")
    parser.add_argument("--user-email", default="developer@docunex.com")
    parser.add_argument("--user-role", default="DEVELOPER")
    parser.add_argument("--subscription-tier", default="PERSONAL")
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(main(parse_args()))
