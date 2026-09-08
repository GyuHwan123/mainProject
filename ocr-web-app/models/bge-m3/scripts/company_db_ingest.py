"""Company RAG database ingestion entry point.

Database replacement is intentionally disabled here. The previous
``ON CONFLICT DO NOTHING`` loader and temporary ``DATABASE_URL`` replacement
flow were removed. A Supabase REST upsert-first workflow will be implemented
separately for the shared baseline/fine-tuned RAG worker.
"""

from __future__ import annotations


def main() -> None:
    raise SystemExit(
        "Company RAG DB replacement is disabled until the Supabase REST "
        "upsert-first worker is implemented."
    )


if __name__ == "__main__":
    main()
