"""Prepare a reviewable seven-row JSON seed; INSERT only with the insert command."""

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.rag_evaluation_demo import create_demo_seed
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
    prepare.add_argument("--batch-id", default="rag-demo-v1")
    prepare.add_argument("--end-date", required=True, type=date.fromisoformat)
    prepare.add_argument("--output", required=True, type=Path)
    insert = commands.add_parser("insert", help="Explicitly INSERT the reviewed JSON; never UPDATE")
    insert.add_argument("--input", required=True, type=Path)
    args = parser.parse_args()

    if args.command == "prepare":
        user_id = developer_id(args.email)
        anchor = repository.latest_rag_evaluation_run(args.email)
        rows = create_demo_seed(user_id, args.batch_id, args.end_date, anchor)
        manifest = {"format": "rag-demo-seed-v1", "email": args.email,
                    "user_id": user_id, "batch_id": args.batch_id,
                    "end_date": args.end_date.isoformat(), "anchor": anchor, "rows": rows}
        with args.output.open("x", encoding="utf-8") as output:
            json.dump(manifest, output, ensure_ascii=False, indent=2, allow_nan=False)
        print(f"Prepared {len(rows)} rows: {args.output}. No database writes.")
        return

    manifest = json.loads(args.input.read_text(encoding="utf-8"))
    if manifest.get("format") != "rag-demo-seed-v1":
        raise ValueError("Unsupported seed format")
    user_id = developer_id(manifest["email"])
    if user_id != manifest["user_id"]:
        raise ValueError("Seed owner does not match the database user")
    rows = create_demo_seed(user_id, manifest["batch_id"],
                            date.fromisoformat(manifest["end_date"]), manifest["anchor"])
    if rows != manifest["rows"]:
        raise ValueError("Seed rows differ from the declared seed metadata; prepare a new file")
    url = f"{repository.url}/rest/v1/rag_evaluation_runs"
    headers = repository._service_headers()
    # UUIDs identify the seven slots of a user/batch. Reject changed dates or
    # values under the same batch rather than silently mixing old and new seeds.
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
                raise ValueError("Existing UUID has different data; use a new batch ID. No rows written.")
    response = httpx.post(
        url, params={"on_conflict": "id"},
        headers={**headers, "Prefer": "resolution=ignore-duplicates,return=minimal"},
        json=rows, timeout=30,
    )
    repository._raise_for_supabase(response, "시연 이력 INSERT 실패")
    print("Seed INSERT complete; existing UUIDs were left unchanged.")


if __name__ == "__main__":
    main()
