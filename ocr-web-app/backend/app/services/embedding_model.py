"""Shared dense embedding loader for RAG queries and frozen company chunks."""
import json
import logging
from pathlib import Path
from typing import Any

from app.core.config import settings


def load_embedding_model(*, device: str | None = None, local_files_only: bool = False,
                         revision: str | None = None) -> Any:
    from sentence_transformers import SentenceTransformer

    source = settings.RAG_EMBEDDING_MODEL
    checkpoint = Path(source)
    if checkpoint.is_dir():
        # Never silently construct a mean-pooling model from an incomplete export.
        modules = json.loads((checkpoint / "modules.json").read_text(encoding="utf-8"))
        if not any(module["type"].endswith(".Pooling") for module in modules):
            raise ValueError("Checkpoint must include its trained SentenceTransformer pooling module")
        local_files_only = True
    model = SentenceTransformer(source, device=device, local_files_only=local_files_only, revision=revision)
    dimension = model.get_sentence_embedding_dimension()
    if dimension != 1024 or settings.RAG_EMBEDDING_DIMENSIONS != 1024:
        raise ValueError(f"RAG requires 1024-dimensional embeddings; loaded {dimension} from {source}")
    logging.getLogger("uvicorn.error").info(
        "RAG embedding model=%s dimension=%s max_seq_length=%s", source, dimension, model.max_seq_length,
    )
    return model
