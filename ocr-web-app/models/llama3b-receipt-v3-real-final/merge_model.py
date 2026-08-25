import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel

BASE_MODEL = "meta-llama/Llama-3.2-3B-Instruct"
ADAPTER_PATH = "."
MERGED_PATH = "./merged"

tokenizer = AutoTokenizer.from_pretrained(ADAPTER_PATH)

base_model = AutoModelForCausalLM.from_pretrained(
    BASE_MODEL,
    dtype=torch.float16,
    device_map="auto"
)

model = PeftModel.from_pretrained(
    base_model,
    ADAPTER_PATH
)

merged_model = model.merge_and_unload()

merged_model.save_pretrained(
    MERGED_PATH,
    safe_serialization=True
)

tokenizer.save_pretrained(MERGED_PATH)

print("✅ merge 완료")
print("저장 위치:", MERGED_PATH)