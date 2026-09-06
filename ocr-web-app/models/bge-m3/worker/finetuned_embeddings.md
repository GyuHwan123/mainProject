# Fine-tuned BGE-M3 staging

`RAG_EMBEDDING_MODEL` selects both online query/document embeddings and the frozen
company v3 embedding worker. Unset/empty defaults to `BAAI/bge-m3`. A local path is
resolved relative to `ocr-web-app`, regardless of the process working directory.
The checkpoint must be a complete merged SentenceTransformer export, including
its trained pooling configuration. Invalid local checkpoints fail without
silently falling back to Base. The common loader requires dimension 1024.

The existing `models/bge-m3/finetuned` directory contains a merged LoRA export.
Use a new, immutable directory for each later checkpoint. Confirm it is the
intended Kaggle export before selecting it.

From the repository root in PowerShell, generate a separate local artifact:

```powershell
$previousEmbeddingModel = $env:RAG_EMBEDDING_MODEL
try {
    $env:RAG_EMBEDDING_MODEL = 'models/bge-m3/finetuned'
    & .\ocr-web-app\backend\venv\Scripts\python.exe .\ocr-web-app\models\bge-m3\worker\generate_embeddings_v3.py --label finetuned-v3
} finally {
    $env:RAG_EMBEDDING_MODEL = $previousEmbeddingModel
}
```

The worker reads the existing 18-document / 115-chunk v3 snapshot, verifies its
frozen fingerprint, and preserves text, ordering, and chunking. It writes
`data/company_documents/embedding_variants_v3/finetuned-v3_embeddings.pkl` and
`finetuned-v3_metadata.json`. Existing output files are never overwritten. The
metadata records checkpoint path, model-file hashes, dimension, chunk fingerprint,
and row mapping. Model path and dimension are printed after loading; backend
loading logs the same information. No DB client is used by this worker.

Keep the running backend on Base until the corresponding vectors are installed
in a separately controlled cutover with a DB backup. At cutover set
`RAG_EMBEDDING_MODEL=models/bge-m3/finetuned` in `ocr-web-app/.env` (and remove any
conflicting backend/environment override), then restart the backend so its model
and embedding cache reload. Query and document vectors must come from the same
immutable checkpoint. Restore both the previous model setting and matching DB
vectors if rolling back. The existing `replace_supabase_v3.py` is a guarded
37-to-115 Base migration, not an automatic fine-tuned vector switch; do not reuse
it for this staging artifact. This preparation does not execute a DB cutover.
