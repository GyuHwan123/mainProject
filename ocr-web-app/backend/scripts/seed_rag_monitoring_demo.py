"""Prepare a fixed 30-day pre-experiment baseline; INSERT only explicitly."""

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.rag_evaluation_demo import create_demo_seed, KST
from app.services.rag_demo_baseline import BASELINE_ID
from app.services.supabase_service import supabase_service as repository


def developer_id(email: str) -> str:
    user = repository.get_user_by_email(email)
    if not user or user.get("role") not in {"DEVELOPER", "ADMIN"}:
        raise ValueError("Seed owner must be an existing DEVELOPER or ADMIN")
    return repository.get_public_user_id(email)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="SELECT only; write a new local JSON file")
    prepare.add_argument("--email", required=True)
    prepare.add_argument("--output", required=True, type=Path)
    insert = commands.add_parser("insert", help="Explicitly INSERT the reviewed JSON; never UPDATE")
    insert.add_argument("--input", required=True, type=Path)
    args = parser.parse_args()

    if args.command == "prepare":
        user_id = developer_id(args.email)
        existing_baseline = repository.list_rag_evaluation_runs(args.email, None, None, demo_batch_id=BASELINE_ID)
        if existing_baseline:
            raise ValueError("Baseline already exists. Keep it fixed; reuse the original JSON for an idempotent retry.")
        actual = repository.list_rag_evaluation_runs(args.email, None, None)
        if not actual:
            raise ValueError("An actual evaluation is required to establish the experiment start date")
        anchor = min(actual, key=lambda row: (datetime.fromisoformat(row["evaluated_at"].replace("Z", "+00:00")), row["id"]))
        end_date = datetime.fromisoformat(anchor["evaluated_at"].replace("Z", "+00:00")).astimezone(KST).date() - timedelta(days=1)
        rows = create_demo_seed(user_id, BASELINE_ID, end_date, anchor)
        manifest = {"format": "rag-demo-baseline", "email": args.email,
                    "user_id": user_id, "batch_id": BASELINE_ID,
                    "end_date": end_date.isoformat(), "anchor": anchor, "rows": rows}
        with args.output.open("x", encoding="utf-8") as output:
            json.dump(manifest, output, ensure_ascii=False, indent=2, allow_nan=False)
        print(f"Prepared {len(rows)} rows: {args.output}. No database writes.")
        return

    manifest = json.loads(args.input.read_text(encoding="utf-8"))
    if manifest.get("format") != "rag-demo-baseline" or manifest.get("batch_id") != BASELINE_ID:
        raise ValueError("Unsupported seed format")
    user_id = developer_id(manifest["email"])
    if user_id != manifest["user_id"]:
        raise ValueError("Seed owner does not match the database user")
    rows = create_demo_seed(user_id, manifest["batch_id"],
                            date.fromisoformat(manifest["end_date"]), manifest["anchor"])
    if rows != manifest["rows"]:
        raise ValueError("Seed rows differ from the declared seed metadata; prepare a new file")
    actual = repository.list_rag_evaluation_runs(manifest["email"], None, None)
    first_day = min((datetime.fromisoformat(row["evaluated_at"].replace("Z", "+00:00")).astimezone(KST).date()
                     for row in actual), default=None)
    if first_day != date.fromisoformat(manifest["end_date"]) + timedelta(days=1):
        raise ValueError("Actual experiment start differs from the prepared baseline; no rows written")
    url = f"{repository.url}/rest/v1/rag_evaluation_runs"
    headers = repository._service_headers()
    # UUIDs identify fixed baseline slots. Never replace existing dates/values.
    existing = httpx.get(url, params={"select": "*", "id": f"in.({','.join(row['id'] for row in rows)})"},
                         headers=headers, timeout=20)
    repository._raise_for_supabase(existing, "시연 배치 중복 확인 실패")
    expected = {row["id"]: row for row in rows}
    for saved in existing.json():
        for key, value in expected[saved["id"]].items():
            actual = saved.get(key)
            if key in {"started_at", "evaluated_at"}:
                actual = datetime.fromisoformat(actual.replace("Z", "+00:00"))
                value = datetime.fromisoformat(value)
            if actual != value:
                raise ValueError("Existing baseline differs. Keep the original baseline; no rows written.")
    response = httpx.post(
        url, params={"on_conflict": "id"},
        headers={**headers, "Prefer": "resolution=ignore-duplicates,return=minimal"},
        json=rows, timeout=30,
    )
    repository._raise_for_supabase(response, "시연 이력 INSERT 실패")
    print("Seed INSERT complete; existing UUIDs were left unchanged.")


if __name__ == "__main__":
    main()
