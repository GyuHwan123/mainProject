"""Train/evaluate a CPU baseline. No extraction model, OCR engine, or network calls.

Usage: python scripts/train_receipt_document_classifier.py --data PATH
Gold expense_category is NEVER used as a feature. The legacy data has no Gemma
outputs: input-only category proxies are explicitly labelled as such in reports.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import platform
import re
import sys
from time import perf_counter

import joblib
import numpy as np
import sklearn
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, f1_score
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.pipeline import Pipeline

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
from app.constants.finance_taxonomy import ALLOWED_DOCUMENT_TYPES
from app.services.receipt_document_classifier import FEATURE_VERSION, feature_text

LABELS = list(ALLOWED_DOCUMENT_TYPES)
NOTE = re.compile(r"^(?:비고|사용목적|업무내용|메모|목적|사용내역|지출사유|용도)\s*[:：]?\s*(.*)$")
META = re.compile(r"^(?:사업자|결제|거래일|판매일|일시|날짜|공급|부가|합계|총액|총금액|TOTAL|PAYMENT|할인|승인|카드|현금)", re.I)


def category_proxy(merchant: str, items: list[dict]) -> str | None:
    """Purchase-object proxy from input only, never purpose or answer fields.

    This approximates a subset of current extraction categories; it does NOT
    predict document types and is not used to replace Gemma at serving time.
    """
    text = " ".join([*(i["name"] for i in items), merchant]).lower()
    rules = [
        (r"주유|연료|휘발유|경유", "주유/차량"),
        (r"택시|ktx|srt|항공|지하철|버스|철도|교통", "대중교통"),
        (r"호텔|숙박|렌터카|리조트", "레저/스포츠"),
        (r"문고|서점|도서|서적|책", "도서"),
        (r"모니터|프린터|키보드|마우스|ssd|아이패드|태블릿|전자|문구|오피스|토너|복사용지|센서|부품|노트북|디스플레이|문서|인쇄|제본", "전자제품/문구"),
        (r"청소|세제|물티슈|다이소", "생활용품"),
        (r"카페|커피|coffee|스타벅스|투썸|이디야|아메리카노", "카페/음료"),
        (r"gs25|cu\b|마트|코스트코|편의점|샌드위치|생수|과자", "식품/장보기"),
        (r"식당|식사|도시락|김밥|한식|치킨|배달|국밥|정식|불고기", "외식/식사"),
    ]
    return next((category for pattern, category in rules if re.search(pattern, text)), None)


def parse_input(text: str) -> dict:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    lines = [line for line in lines if not line.startswith("다음 영수증")]
    merchant, items, notes = "", [], []
    for line in lines:
        note = NOTE.match(line)
        if note:
            notes.append(note[1])
        elif not merchant:
            merchant = re.sub(r"^(?:매장명|상호|가맹점명|가맹점|업체명)\s*[:：]?\s*", "", line)
        elif not META.match(line):
            name = re.sub(r"\s+[\dOo][\dOo,.]*(?:\s*원)?(?:\s.*)?$", "", line).strip()
            if name and not re.fullmatch(r"[\d\W]+", name):
                items.append({"name": name})
    return {"merchant": merchant, "items": items,
            "transaction_description": " ".join(notes),
            "expense_category": category_proxy(merchant, items)}


def load_rows(path: Path) -> tuple[list[dict], dict]:
    rows, cats, counts = [], defaultdict(Counter), Counter()
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        user = next(m["content"] for m in raw["messages"] if m["role"] == "user")
        answer = json.loads(next(m["content"] for m in raw["messages"] if m["role"] == "assistant"))
        label = answer["doc_type"]
        if label not in LABELS and not (label is None and answer["needs_review"] is True):
            raise ValueError("unexpected training label")
        fields = parse_input(user)
        rows.append({"id": raw["source_id"], "label": label, "fields": fields,
                     "input_hash": hashlib.sha256(user.encode()).hexdigest()})
        counts[label or "REVIEW_NULL"] += 1
        cats[str(answer["expense_category"])][label or "REVIEW_NULL"] += 1
    return rows, {"rows": len(rows), "label_counts": counts, "gold_category_labels": cats,
                  "unique_ids": len({r["id"] for r in rows}),
                  "unique_inputs": len({r["input_hash"] for r in rows}),
                  "description_present": sum(bool(r["fields"]["transaction_description"]) for r in rows),
                  "items_present": sum(bool(r["fields"]["items"]) for r in rows),
                  "proxy_category_present": sum(bool(r["fields"]["expense_category"]) for r in rows)}


def grouped_splits(rows: list[dict]) -> tuple[dict, list[int]]:
    # Connected components prevent both identical visible receipts and repeated
    # synthetic purpose phrases from leaking across train/validation/test.
    parent = list(range(len(rows)))
    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    seen = {}
    for i, row in enumerate(rows):
        fields = row["fields"]
        keys = ["receipt:" + feature_text(fields, with_category=False)]
        if fields["transaction_description"]:
            keys.append("purpose:" + re.sub(r"\W", "", fields["transaction_description"]))
        for key in keys:
            if key in seen:
                parent[root(i)] = root(seen[key])
            else:
                seen[key] = i
    groups = [root(i) for i in range(len(rows))]
    labels = [r["label"] or "REVIEW_NULL" for r in rows]
    folds = list(StratifiedGroupKFold(5, shuffle=True, random_state=42).split(rows, labels, groups))
    test, val = folds[0][1].tolist(), folds[1][1].tolist()
    train = sorted(set(range(len(rows))) - set(test) - set(val))
    splits = {"train": train, "validation": val, "test": test}
    for a, b in [("train", "validation"), ("train", "test"), ("validation", "test")]:
        assert not {groups[i] for i in splits[a]} & {groups[i] for i in splits[b]}
    for name, indices in splits.items():
        if {rows[i]["label"] for i in indices if rows[i]["label"]} != set(LABELS):
            raise ValueError(f"{name} lacks classes after grouped split; collect more independent data")
    return splits, groups


def threshold_table(y, probabilities, classes):
    predictions = np.asarray(classes)[probabilities.argmax(axis=1)]
    confidence = probabilities.max(axis=1)
    result = []
    for threshold in [0.0, .4, .5, .6, .7, .8, .85, .9, .95, .99]:
        accepted = confidence >= threshold
        per_class = {}
        for label in LABELS:
            mask = accepted & (predictions == label)
            per_class[label] = {"accepted": int(mask.sum()),
                                "precision": float((predictions[mask] == y[mask]).mean()) if mask.any() else None}
        result.append({"threshold": threshold, "accepted": int(accepted.sum()),
                       "coverage": float(accepted.mean()),
                       "accuracy": float((predictions[accepted] == y[accepted]).mean()) if accepted.any() else None,
                       "per_class": per_class})
    return result


def evaluate(model, rows, indices, profile):
    x = [feature_text(rows[i]["fields"], **profile) for i in indices]
    y = np.array([rows[i]["label"] or "REVIEW_NULL" for i in indices])
    probabilities = model.predict_proba(x)
    predictions = model.classes_[probabilities.argmax(axis=1)]
    labeled = y != "REVIEW_NULL"
    return {"labeled_count": int(labeled.sum()), "review_null_count": int((~labeled).sum()),
            "accuracy": accuracy_score(y[labeled], predictions[labeled]),
            "macro_f1": f1_score(y[labeled], predictions[labeled], labels=LABELS, average="macro", zero_division=0),
            "classification_report": classification_report(y[labeled], predictions[labeled], labels=LABELS, output_dict=True, zero_division=0),
            "confusion_matrix_labels": LABELS,
            "confusion_matrix": confusion_matrix(y[labeled], predictions[labeled], labels=LABELS).tolist(),
            "thresholds_labeled": threshold_table(y[labeled], probabilities[labeled], model.classes_),
            "thresholds_including_review": threshold_table(y, probabilities, model.classes_)}, y, probabilities


def select_thresholds(table, target_precision, min_support):
    # Selection sees validation only. Null labels count as incorrect auto-routing.
    thresholds = {}
    for label in LABELS:
        eligible = [r for r in table if r["per_class"][label]["accepted"] >= min_support
                    and r["per_class"][label]["precision"] >= target_precision]
        thresholds[label] = min((r["threshold"] for r in eligible), default=None)
    return thresholds


def selective_result(y, probabilities, classes, thresholds):
    predictions = np.asarray(classes)[probabilities.argmax(axis=1)]
    accepted = np.array([thresholds[p] is not None and c >= thresholds[p]
                         for p, c in zip(predictions, probabilities.max(axis=1))])
    return {"accepted": int(accepted.sum()), "coverage": float(accepted.mean()),
            "accuracy": float((predictions[accepted] == y[accepted]).mean()) if accepted.any() else None,
            "null_false_accepts": int((accepted & (y == "REVIEW_NULL")).sum())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=BACKEND / "data/document-classifier")
    parser.add_argument("--report", type=Path, default=BACKEND.parent / "reports/document-classifier-evaluation.json")
    parser.add_argument("--target-precision", type=float, default=.95)
    parser.add_argument("--min-support", type=int, default=10)
    args = parser.parse_args()
    rows, audit = load_rows(args.data)
    splits, groups = grouped_splits(rows)
    report = {"audit": audit, "data_sha256": hashlib.sha256(args.data.read_bytes()).hexdigest(),
              "sklearn_version": sklearn.__version__, "python": platform.python_version(),
              "platform": platform.platform(), "group_count": len(set(groups)),
              "split_counts": {s: Counter(rows[i]["label"] or "REVIEW_NULL" for i in ids) for s, ids in splits.items()},
              "split_ids": {s: [rows[i]["id"] for i in ids] for s, ids in splits.items()},
              "selection_policy": {"target_precision": args.target_precision, "min_support": args.min_support,
                                   "note": "Exploratory risk target, not a production guarantee; null cases count as errors."},
              "experiments": {}}
    profiles = {
        "production_no_category": {"with_description": False, "with_category": False},
        "production_proxy_category": {"with_description": False, "with_category": True},
        "context_proxy_category": {"with_description": True, "with_category": True},
    }
    models, selections = {}, {}
    for name, profile in profiles.items():
        train = [i for i in splits["train"] if rows[i]["label"]]
        model = Pipeline([("tfidf", TfidfVectorizer(analyzer="char", ngram_range=(2, 5),
                                                   min_df=2, max_features=30000, sublinear_tf=True)),
                          ("lr", LogisticRegression(C=4.0, max_iter=1000, random_state=42))])
        model.fit([feature_text(rows[i]["fields"], **profile) for i in train], [rows[i]["label"] for i in train])
        validation, y, probabilities = evaluate(model, rows, splits["validation"], profile)
        thresholds = select_thresholds(validation["thresholds_including_review"], args.target_precision, args.min_support)
        selective = selective_result(y, probabilities, model.classes_, thresholds)
        report["experiments"][name] = {"profile": profile, "validation": validation,
                                       "recommended_thresholds": thresholds, "validation_selective": selective}
        models[name], selections[name] = model, selective
    # Report the empirical baseline winner, but the serving contract includes
    # category. Ship that candidate in REVIEW mode even if no class qualifies.
    report["validation_baseline_winner"] = max((n for n in profiles if n.startswith("production_")),
                   key=lambda n: (selections[n]["coverage"], report["experiments"][n]["validation"]["macro_f1"]))
    selected = "production_proxy_category"
    report["deployment_selection_reason"] = "Requested category + merchant + items contract; REVIEW-only synthetic candidate. No test-based model selection."
    report["selected_profile"] = selected
    for name, model in models.items():
        experiment = report["experiments"][name]
        test, y, probabilities = evaluate(model, rows, splits["test"], profiles[name])
        experiment["test"] = test
        experiment["test_selective"] = selective_result(y, probabilities, model.classes_, experiment["recommended_thresholds"])
        samples = [feature_text(rows[i]["fields"], **profiles[name]) for i in splits["test"]]
        model.predict_proba(samples[:1])
        times = []
        for j in range(300):
            started = perf_counter()
            model.predict_proba([samples[j % len(samples)]])
            times.append((perf_counter() - started) * 1000)
        experiment["inference_ms"] = {"mean": float(np.mean(times)), "p50": float(np.median(times)), "p95": float(np.percentile(times, 95)), "calls": len(times), "batch_size": 1, "includes": "TF-IDF + LogisticRegression; warm CPU"}
    artifact = {"feature_version": FEATURE_VERSION, "model_version": "tfidf-lr-v1-" + report["data_sha256"][:12],
                "model": models[selected], "profile": profiles[selected],
                "thresholds": report["experiments"][selected]["recommended_thresholds"],
                "production_validated": False, "data_sha256": report["data_sha256"],
                "sklearn_version": sklearn.__version__}
    args.output.mkdir(parents=True, exist_ok=True)
    joblib.dump(artifact, args.output / "model.joblib", compress=3)
    report["artifact_bytes"] = (args.output / "model.joblib").stat().st_size
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ["audit", "group_count", "split_counts", "selected_profile", "artifact_bytes"]}, ensure_ascii=False, indent=2))
    for name, experiment in report["experiments"].items():
        print(name, json.dumps({"accuracy": experiment["test"]["accuracy"], "macro_f1": experiment["test"]["macro_f1"], "thresholds": experiment["recommended_thresholds"], "test_selective": experiment["test_selective"], "latency": experiment["inference_ms"]}))


if __name__ == "__main__":
    main()
