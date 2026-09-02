import argparse
import csv
import json
from pathlib import Path


def run(input_path: str, output_path: str) -> None:
    with Path(input_path).open("r", encoding="utf-8") as f:
        data = json.load(f)

    rows = []

    for case in data["cases"]:
        qid = case["question_id"]
        query = case["query"]
        expected_documents = ", ".join(case.get("expected_documents", []))

        positives = case.get("positive_candidates", [])
        hard_negatives = case.get("hard_negatives", [])

        # Positive 검수 행
        for idx, item in enumerate(positives, 1):
            rows.append(
                {
                    "question_id": qid,
                    "query": query,
                    "type": "positive",
                    "rank": idx,
                    "expected_documents": expected_documents,
                    "document": item.get("document", ""),
                    "rerank_score": item.get("rerank_score", ""),
                    "vector_similarity": item.get("vector_similarity", ""),
                    "content": item.get("content", ""),
                    "review": "",
                    "memo": "",
                }
            )

        # Hard Negative 검수 행
        for idx, item in enumerate(hard_negatives, 1):
            rows.append(
                {
                    "question_id": qid,
                    "query": query,
                    "type": "hard_negative",
                    "rank": idx,
                    "expected_documents": expected_documents,
                    "document": item.get("document", ""),
                    "rerank_score": item.get("rerank_score", ""),
                    "vector_similarity": item.get("vector_similarity", ""),
                    "content": item.get("content", ""),
                    "review": "",
                    "memo": "",
                }
            )

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "question_id",
        "query",
        "type",
        "rank",
        "expected_documents",
        "document",
        "rerank_score",
        "vector_similarity",
        "content",
        "review",
        "memo",
    ]

    with output.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"검수용 CSV 생성 완료: {output}")
    print(f"총 행 수: {len(rows)}")


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("input_json")
    parser.add_argument("--output", default="hard_negative_review.csv")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run(args.input_json, args.output)