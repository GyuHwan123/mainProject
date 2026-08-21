"""Run the trained Qwen2-VL adapter as an HTTP service in Google Colab.

Copy this file into Colab (or paste it into a cell), install fastapi/uvicorn,
then expose port 8002 with the tunnel provider of your choice. Keep the token
secret and configure the resulting /predict URL as QWEN_VL_API_URL.
"""

import base64
import io
import os
from contextlib import asynccontextmanager

import torch
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from PIL import Image
from unsloth import FastVisionModel


ADAPTER_PATH = os.getenv("QWEN_VL_ADAPTER_PATH", "/content/drive/MyDrive/qwen2vl_receipt_output/best_adapter")
API_TOKEN = os.getenv("QWEN_VL_API_TOKEN", "")
MODEL = None
TOKENIZER = None


class PredictRequest(BaseModel):
    image_base64: str
    mime_type: str = "image/jpeg"
    filename: str = "receipt.jpg"
    prompt: str


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global MODEL, TOKENIZER
    MODEL, TOKENIZER = FastVisionModel.from_pretrained(ADAPTER_PATH, load_in_4bit=True)
    FastVisionModel.for_inference(MODEL)
    yield


app = FastAPI(title="Qwen2-VL receipt inference", lifespan=lifespan)


@app.get("/health")
def health():
    return {"ready": MODEL is not None}


@app.post("/predict")
def predict(payload: PredictRequest, authorization: str | None = Header(default=None)):
    if API_TOKEN and authorization != f"Bearer {API_TOKEN}":
        raise HTTPException(status_code=401, detail="invalid token")
    if not payload.mime_type.startswith("image/"):
        raise HTTPException(status_code=415, detail="Qwen-VL endpoint accepts image files")
    try:
        image = Image.open(io.BytesIO(base64.b64decode(payload.image_base64))).convert("RGB")
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid image") from exc
    messages = [{"role": "user", "content": [
        {"type": "image", "image": image},
        {"type": "text", "text": payload.prompt},
    ]}]
    input_text = TOKENIZER.apply_chat_template(messages, add_generation_prompt=True)
    inputs = TOKENIZER(image, input_text, add_special_tokens=False, return_tensors="pt").to("cuda")
    with torch.no_grad():
        outputs = MODEL.generate(**inputs, max_new_tokens=900, do_sample=False, use_cache=True)
    new_tokens = outputs[:, inputs["input_ids"].shape[1]:]
    return {"output": TOKENIZER.batch_decode(new_tokens, skip_special_tokens=True)[0]}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)
