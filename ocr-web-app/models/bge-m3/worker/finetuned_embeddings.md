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

## Embedding-only cutover

Use `cutover_embeddings_v3.py` for the completed fine-tuned artifact. It does not
import or reuse `replace_supabase_v3.py`. It PATCHes only `rag_chunks.embedding`,
with exact row ID, document UUID and chunk index filters. Before any PATCH it
verifies artifact/checkpoint hashes, float32/1024 dimensions, normalization,
frozen row mapping, text hashes and pages, and a bijection with all 115 current
company rows across the 18 canonical documents. No chunk is inserted or deleted.

1. Stop the backend with Ctrl+C in its running terminal, and stop other ingestion
   or DB writers. Keep them stopped through completion or rollback: REST PATCHes
   are not a single transaction, so intermediate vectors must not serve traffic.
2. From the repository root, optionally prepare a backup without DB writes:

   ```powershell
   .\ocr-web-app\backend\venv\Scripts\python.exe .\ocr-web-app\models\bge-m3\worker\cutover_embeddings_v3.py
   ```

3. Apply. This performs preflight again and creates a fresh, fsynced backup before
   the first write; it does not rely on a stale prepare result:

   ```powershell
   .\ocr-web-app\backend\venv\Scripts\python.exe .\ocr-web-app\models\bge-m3\worker\cutover_embeddings_v3.py --apply
   ```

   A unique folder under `data/company_documents/embedding_cutover_backups` stores
   `backup.json` and `backup.sha256`, including original rows, target vectors,
   document mapping and artifact identity. Do not edit these files. Successful
   completion prints `CUTOVER PASS` after rereading all rows and checking that
   all non-embedding fields, including metadata, remain exactly unchanged.

4. Only after PASS, set this value in `ocr-web-app/.env`:

   ```dotenv
   RAG_EMBEDDING_MODEL=models/bge-m3/finetuned
   ```

   Remove conflicting `RAG_EMBEDDING_MODEL` overrides in `backend/.env` or the
   process environment. Start a fresh backend process from the repository root:

   ```powershell
   .\ocr-web-app\backend\venv\Scripts\python.exe -m uvicorn main:app --app-dir .\ocr-web-app\backend --host 127.0.0.1 --port 8000
   ```

On an update/verification failure, automatic rollback attempts to restore every
changed vector, including a timed-out PATCH that may have committed. If the
process is killed or the network remains unavailable, keep the backend stopped
and use the printed backup path when connectivity is restored:

```powershell
.\ocr-web-app\backend\venv\Scripts\python.exe .\ocr-web-app\models\bge-m3\worker\cutover_embeddings_v3.py --rollback '<absolute path to backup.json>'
```

Rollback is repeatable and does not require the embedding artifact/checkpoint.
It validates the backup checksum and endpoint, then checks all 115 rows before
writing. It restores only vectors equal to this cutover's target and leaves
already-restored vectors alone. Unexpected vectors or changed non-embedding
fields stop restoration to avoid overwriting another writer's changes. If rollback
does not print PASS, do not restart service. After successful rollback restore
the previous model setting (Base for this cutover) before restarting the backend.
