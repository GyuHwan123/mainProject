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
        # This export uses an obsolete Normalize import path and omits its
        # parameter-free directory. Load the exact trained modules without
        # rewriting the checkpoint or falling back to a pretrained model.
        from sentence_transformers.base.modules.transformer import Transformer
        from sentence_transformers.base.modules.normalize import Normalize
        from sentence_transformers.sentence_transformer.modules.pooling import Pooling

        if [module["type"].rsplit(".", 1)[-1] for module in modules] != ["Transformer", "Pooling", "Normalize"]:
            raise ValueError(f"Unsupported embedding checkpoint module layout: {source}")
        loaded_modules = [
            Transformer.load(source, subfolder=modules[0]["path"], local_files_only=True),
            Pooling.load(source, subfolder=modules[1]["path"], local_files_only=True),
            Normalize(),
        ]
        model = SentenceTransformer(modules=loaded_modules, device=device)
        # Convert only the in-memory local model on CPU. Preserve checkpoint
        # files and the existing dtype policy on CUDA/other accelerators.
        if next(model.parameters()).device.type == "cpu":
            import torch

            model.to(dtype=torch.float32)
    else:
        if source != "BAAI/bge-m3":
            raise ValueError(f"RAG embedding checkpoint directory not found: {source}")
        model = SentenceTransformer(source, device=device, local_files_only=local_files_only, revision=revision)
    dimension = model.get_sentence_embedding_dimension()
    if dimension != 1024 or settings.RAG_EMBEDDING_DIMENSIONS != 1024:
        raise ValueError(f"RAG requires 1024-dimensional embeddings; loaded {dimension} from {source}")
    parameter = next(model.parameters())
    logging.getLogger("uvicorn.error").info(
        "RAG embedding model=%s device=%s dtype=%s dimension=%s max_seq_length=%s",
        source, parameter.device, parameter.dtype, dimension, model.max_seq_length,
    )
    return model
