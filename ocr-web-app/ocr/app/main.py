from fastapi import FastAPI
from app.api.routes.ocr import router

app = FastAPI(title="OCR Server")

app.include_router(router)


@app.get("/health")
def health():
    return {"status": "ok"}