import os
from pathlib import Path

from fastapi import FastAPI

# Keep model files in a writable, project-local cache. This must be configured
# before importing the router because PaddleOCR is initialized during import.
OCR_ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(OCR_ROOT / ".paddlex"))
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")

from app.api.routes.ocr import router

app = FastAPI(title="OCR Server")

app.include_router(router)


@app.get("/health")
def health():
    return {"status": "ok"}
