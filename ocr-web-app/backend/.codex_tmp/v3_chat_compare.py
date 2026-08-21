import hashlib
import ast
import json
from pathlib import Path
import time

import httpx

source = Path(__file__).with_name("v3_stability_test.py").read_text(encoding="utf-8")
tree = ast.parse(source)
assignments = [
    node for node in tree.body
    if isinstance(node, ast.Assign)
    and any(isinstance(target, ast.Name) and target.id in {"ocr", "prompt"} for target in node.targets)
]
values = {}
exec(compile(ast.Module(body=assignments, type_ignores=[]), "prompt_assignments", "exec"), values)
ocr = values["ocr"]
prompt = values["prompt"]

options = {"temperature": 0, "num_predict": 1200, "num_ctx": 8192, "repeat_penalty": 1.08}
models = ["llama3b-receipt-v3:latest", "llama3b-receipt-v3-chat-test"]
print("OCR_LENGTH:", len(ocr))
print("PROMPT_LENGTH:", len(prompt))
print("PROMPT_SHA256:", hashlib.sha256(prompt.encode("utf-8")).hexdigest())
print("OPTIONS:", json.dumps({"format": "json", "options": options}, ensure_ascii=False, sort_keys=True))

with httpx.Client(timeout=900.0) as client:
    for model in models:
        payload = {"model": model, "prompt": prompt, "stream": False, "keep_alive": "30m", "format": "json", "options": options}
        started = time.perf_counter()
        response = client.post("http://127.0.0.1:11434/api/generate", json=payload)
        response.raise_for_status()
        body = response.json()
        raw = body.get("response", "")
        parsed = json.loads(raw)
        arrays = {key: len(value) for key, value in parsed.items() if isinstance(value, list)}
        print(f"=== {model} RAW BEGIN ===")
        print(raw)
        print(f"=== {model} RAW END ===")
        print("SUMMARY:", json.dumps({"array_counts": arrays, "top_keys": list(parsed.keys()), "seconds": round(time.perf_counter() - started, 1), "eval_count": body.get("eval_count")}, ensure_ascii=False))
