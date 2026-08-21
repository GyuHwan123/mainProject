from pathlib import Path

import pymupdf


base = Path(__file__).resolve().parent
pdf_path = base / "rendered" / "LLM_영수증_구조화_성능평가_보고서.pdf"
out_dir = base / "rendered"
document = pymupdf.open(pdf_path)
for index, page in enumerate(document):
    pixmap = page.get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5), alpha=False)
    pixmap.save(out_dir / f"page-{index + 1}.png")
print(f"rendered {len(document)} pages")
