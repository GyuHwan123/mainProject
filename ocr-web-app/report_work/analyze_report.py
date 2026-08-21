import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.finance_evaluation_service import (  # noqa: E402
    normalize_ground_truth,
    score_fields,
)

STATS_PATH = Path(r"C:\Users\2Class_08\Downloads\finance-model-batch-20-statistics (1).json")
TRUTH_PATH = Path(r"C:\Users\2Class_08\Downloads\라벨링데이터-20260821T030610Z-1-001\라벨링데이터\test01_test20_ground_truth.json")
OUT_PATH = Path(__file__).with_name("analysis.json")

stats = json.loads(STATS_PATH.read_text(encoding="utf-8"))
truth_rows = json.loads(TRUTH_PATH.read_text(encoding="utf-8"))
truth_by_image = {row["image"].lower(): normalize_ground_truth(row) for row in truth_rows}

model_rows = defaultdict(list)
all_cases = []
for run in stats["runs"]:
    image = (run.get("matched_image") or Path(run.get("document_name", "")).name).lower()
    truth = truth_by_image[image]
    for result in run.get("results", []):
        model = result["model_name"]
        prediction = result.get("system", {}).get("prediction", {})
        corrected = score_fields(prediction, truth)
        stored = result.get("system", {}).get("score", {})
        row = {
            "image": image,
            "latency_ms": result.get("latency_ms", 0),
            "stored_accuracy": stored.get("field_accuracy", 0),
            "corrected_accuracy": corrected.get("field_accuracy", 0),
            "corrected_score": corrected,
            "prediction": prediction,
            "truth": truth,
        }
        model_rows[model].append(row)
        all_cases.append({"model": model, **row})

summaries = {}
for model, rows in model_rows.items():
    field_counts = Counter()
    field_totals = Counter()
    item_count_correct = 0
    complete = 0
    zero_item_outputs = 0
    present_only_correct = Counter()
    present_only_totals = Counter()
    for row in rows:
        score = row["corrected_score"]
        complete += int(score["complete_match"])
        for field, detail in score["fields"].items():
            if field == "items":
                item_count_correct += int(detail["count_correct"])
                zero_item_outputs += int(detail["actual_count"] == 0)
                field_totals["items.count"] += 1
                field_counts["items.count"] += int(detail["count_correct"])
                for item in detail.get("items", []):
                    for item_field, item_detail in item.get("fields", {}).items():
                        key = f"items.{item_field}"
                        field_totals[key] += 1
                        field_counts[key] += int(item_detail["correct"])
            else:
                field_totals[field] += 1
                field_counts[field] += int(detail["correct"])
                expected = detail.get("expected")
                if expected not in (None, "", []):
                    present_only_totals[field] += 1
                    present_only_correct[field] += int(detail["correct"])

    official = next(item for item in stats["model_statistics"] if item["model"] == model)
    summaries[model] = {
        "documents": len(rows),
        "official": official,
        "stored_mean_field_accuracy": sum(r["stored_accuracy"] for r in rows) / len(rows),
        "corrected_mean_field_accuracy": sum(r["corrected_accuracy"] for r in rows) / len(rows),
        "complete_matches": complete,
        "zero_item_outputs": zero_item_outputs,
        "item_count_accuracy": item_count_correct / len(rows),
        "field_accuracy": {
            field: field_counts[field] / total
            for field, total in sorted(field_totals.items())
        },
        "field_correct_counts": dict(field_counts),
        "field_totals": dict(field_totals),
        "present_only_accuracy": {
            field: present_only_correct[field] / total
            for field, total in sorted(present_only_totals.items())
        },
        "present_only_counts": {
            field: {"correct": present_only_correct[field], "total": total}
            for field, total in sorted(present_only_totals.items())
        },
    }

worst_cases = {}
for model, rows in model_rows.items():
    ranked = sorted(rows, key=lambda row: (row["corrected_accuracy"], row["image"]))
    worst_cases[model] = [
        {
            "image": row["image"],
            "accuracy": row["corrected_accuracy"],
            "errors": [
                field if field != "items" else "items"
                for field, detail in row["corrected_score"]["fields"].items()
                if not (detail.get("correct") if field != "items" else detail.get("count_correct"))
            ],
            "prediction": row["prediction"],
            "truth": row["truth"],
        }
        for row in ranked[:5]
    ]

output = {
    "source_stats": str(STATS_PATH),
    "source_truth": str(TRUTH_PATH),
    "exported_at": stats.get("exported_at"),
    "evaluated_images": stats.get("evaluated_images"),
    "models": stats.get("models"),
    "summaries": summaries,
    "worst_cases": worst_cases,
    "export_field_error_counts": stats.get("field_error_counts"),
}
OUT_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(output, ensure_ascii=False, indent=2))
