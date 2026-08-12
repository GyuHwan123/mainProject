import re
import unicodedata


def normalize_ocr_text(text: str) -> str:
    """Normalize OCR artifacts without guessing or rewriting recognized words."""
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("\u200b", "").replace("\ufeff", "")
    text = re.sub(r"[\t\f\v]+", " ", text)
    text = re.sub(r" {2,}", " ", text)
    return text.strip()
