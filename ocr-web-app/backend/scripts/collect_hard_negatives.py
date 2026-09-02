from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any


BACKEND_DIR = Path(__file__).resolve().parents[1]

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


import app.services.rag_service as rag


def compact(value: Any) -> str:
    text = str(value or "").lower()
    text = re.sub(r"\.[a-z0-9]{1,6}$", "", text)
    return re.sub(r"[^0-9a-z가-힣]+", "", text)


def is_expected(
    candidate: dict[str, Any],
    case: dict[str, Any],
) -> bool:

    expected_values = [
        *case.get("expected_documents", []),
        *case.get("expected_document_titles", []),
    ]

    expected = [
        compact(value)
        for value in expected_values
        if compact(value)
    ]

    candidate_values = []

    for key, value in candidate.items():
        if isinstance(value, (str, int, float)):
            candidate_values.append(str(value))

    got_values = [
        compact(value)
        for value in candidate_values
        if compact(value)
    ]

    return any(
        exp == got
        or exp in got
        or got in exp
        for exp in expected
        for got in got_values
        if len(exp) >= 3 and len(got) >= 3
    )


def candidate_label(
    candidate: dict[str, Any],
) -> str:

    for key in (
        "source",
        "document_code",
        "document_title",
        "file_name",
        "title",
        "document_id",
    ):
        if candidate.get(key):
            return str(candidate[key])

    return str(
        candidate.get("id")
        or "(unknown)"
    )


def make_item(
    candidate: dict[str, Any],
) -> dict[str, Any]:

    return {
        "document": candidate_label(candidate),
        "content": candidate.get("content", ""),
        "rerank_score": candidate.get(
            "rerank_score"
        ),
        "vector_similarity": candidate.get(
            "vector_similarity"
        ),
    }


async def retrieve_candidates(
    user_email: str,
    query: str,
    user_role: str,
    subscription_tier: str,
    candidate_limit: int = 6,
) -> list[dict[str, Any]]:

    facets = rag._extract_evidence_facets(query)

    strong_subjects = facets["strong_subjects"]

    query_texts = [
        query,
        facets["query"],
        *strong_subjects,
    ]

    query_vectors, _ = await rag._embed_texts_cached(
        query_texts
    )

    embedding = query_vectors[0]

    candidates = (
        rag.supabase_service.search_rag_chunks(
            user_email,
            embedding,
            None,
            candidate_limit,
            include_company_documents=(
                rag.can_access_company_rag(
                    user_role,
                    subscription_tier,
                )
            ),
        )
    )

    compact_query = "".join(
        query.lower().split()
    )

    requested_sections = [
        keywords
        for name, keywords
        in rag.SECTION_KEYWORDS.items()
        if name in compact_query
    ]

    query_terms = {
        token
        for token in (
            query.lower()
            .replace("?", " ")
            .replace(".", " ")
            .split()
        )
        if len(token) >= 2
        and token
        not in {
            "어떻게",
            "알려줘",
            "알려주세요",
            "무엇",
            "뭐야",
            "지원자",
            "지원자의",
        }
    }

    for row in candidates:

        content = str(
            row.get("content", "")
        ).lower()

        section_hits = sum(
            1
            for keywords in requested_sections
            for keyword in keywords
            if keyword in content
        )

        term_hits = sum(
            1
            for term in query_terms
            if term.rstrip(
                "은는이가을를의"
            )
            in content
        )

        lexical_boost = min(
            0.45,
            section_hits * 0.07
            + term_hits * 0.04,
        )

        row["vector_similarity"] = float(
            row.get("similarity")
            or 0
        )

        row["similarity"] = min(
            1.0,
            row["vector_similarity"]
            + lexical_boost,
        )

    candidates.sort(
        key=lambda row: float(
            row.get("similarity")
            or 0
        ),
        reverse=True,
    )

    candidates = await rag.rerank_candidates(
        query,
        candidates,
    )

    return candidates


async def run(
    args: argparse.Namespace,
) -> None:

    ground_truth_path = Path(
        args.ground_truth
    )

    with ground_truth_path.open(
        "r",
        encoding="utf-8",
    ) as f:
        dataset = json.load(f)

    results = []

    total_hard_negatives = 0
    extended_search_count = 0

    for case in dataset["cases"]:

        # 정답 없음 질문은 Hard Negative 학습에서 제외
        if not case.get(
            "answerable",
            False,
        ):
            continue

        qid = case["question_id"]
        question = case["question"]

        # -------------------------------------------------
        # 1차: 현재 운영 설정과 동일하게 Top-6 검색
        # -------------------------------------------------

        candidates = await retrieve_candidates(
            args.user_email,
            question,
            args.user_role,
            args.subscription_tier,
            candidate_limit=6,
        )

        positives = []
        hard_negatives = []

        for candidate in candidates:

            item = make_item(
                candidate
            )

            if is_expected(
                candidate,
                case,
            ):
                positives.append(
                    item
                )

            else:
                hard_negatives.append(
                    item
                )

        # -------------------------------------------------
        # 2차:
        # Top-6에서 Hard Negative가 3개 미만이면
        # 수집 스크립트에서만 Top-20까지 추가 탐색
        # -------------------------------------------------

        used_extended_search = False

        if len(hard_negatives) < 3:

            used_extended_search = True
            extended_search_count += 1

            extended_candidates = (
                await retrieve_candidates(
                    args.user_email,
                    question,
                    args.user_role,
                    args.subscription_tier,
                    candidate_limit=20,
                )
            )

            # 기존 Hard Negative 중복 방지
            seen = {
                (
                    item["document"],
                    item["content"],
                )
                for item
                in hard_negatives
            }

            for candidate in (
                extended_candidates
            ):

                # 정답 문서는 제외
                if is_expected(
                    candidate,
                    case,
                ):
                    continue

                item = make_item(
                    candidate
                )

                key = (
                    item["document"],
                    item["content"],
                )

                # 이미 저장된 청크면 제외
                if key in seen:
                    continue

                hard_negatives.append(
                    item
                )

                seen.add(
                    key
                )

                # 질문당 최대 3개
                if (
                    len(
                        hard_negatives
                    )
                    >= 3
                ):
                    break

        # 최종적으로 질문당 최대 3개만 저장
        selected_hard_negatives = (
            hard_negatives[:3]
        )

        total_hard_negatives += len(
            selected_hard_negatives
        )

        results.append(
            {
                "question_id": qid,
                "query": question,
                "expected_documents": (
                    case.get(
                        "expected_documents",
                        [],
                    )
                ),
                "positive_candidates": (
                    positives
                ),
                "hard_negatives": (
                    selected_hard_negatives
                ),
                "extended_search_used": (
                    used_extended_search
                ),
            }
        )

        search_label = (
            "Top-20 보충"
            if used_extended_search
            else "Top-6"
        )

        print(
            f"{qid} | "
            f"positive={len(positives)} | "
            f"hard_negative="
            f"{len(selected_hard_negatives)} | "
            f"{search_label}"
        )

    output_path = Path(
        args.output
    )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    output_data = {
        "dataset": (
            "hard_negative_v2"
        ),
        "source_ground_truth": str(
            ground_truth_path
        ),
        "answerable_question_count": len(
            results
        ),
        "hard_negative_count": (
            total_hard_negatives
        ),
        "extended_search_count": (
            extended_search_count
        ),
        "cases": results,
    }

    with output_path.open(
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            output_data,
            f,
            ensure_ascii=False,
            indent=2,
        )

    print()
    print(
        "=============================="
    )
    print(
        "Hard Negative 수집 완료"
    )
    print(
        "=============================="
    )
    print(
        f"총 질문: {len(results)}"
    )
    print(
        "Hard Negative 총 개수: "
        f"{total_hard_negatives}"
    )
    print(
        "Top-20 보충 사용 질문: "
        f"{extended_search_count}"
    )
    print(
        f"저장 완료: {output_path}"
    )


def parse_args() -> argparse.Namespace:

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "ground_truth",
        help=(
            "ground_truth JSON 경로"
        ),
    )

    parser.add_argument(
        "--output",
        default=(
            "hard_negatives_v2.json"
        ),
    )

    parser.add_argument(
        "--user-email",
        default=(
            "developer@docunex.com"
        ),
    )

    parser.add_argument(
        "--user-role",
        default="DEVELOPER",
    )

    parser.add_argument(
        "--subscription-tier",
        default="PERSONAL",
    )

    return parser.parse_args()


if __name__ == "__main__":

    asyncio.run(
        run(
            parse_args()
        )
    )