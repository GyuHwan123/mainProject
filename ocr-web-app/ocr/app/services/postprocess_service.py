import re
import unicodedata


def normalize_ocr_text(text: str) -> str:
    """Normalize OCR artifacts without guessing or rewriting recognized words."""
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("\u200b", "").replace("\ufeff", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[\t\f\v ]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()
