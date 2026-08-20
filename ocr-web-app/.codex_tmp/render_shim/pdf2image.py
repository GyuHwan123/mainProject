from pathlib import Path

import fitz
from PIL import Image


def pdfinfo_from_path(pdf_path, **_kwargs):
    document = fitz.open(pdf_path)
    try:
        page = document[0]
        width = page.rect.width
        height = page.rect.height
        return {
            "Pages": len(document),
            "Page size": f"{width:.1f} x {height:.1f} pts",
        }
    finally:
        document.close()


def convert_from_path(
    pdf_path,
    dpi=200,
    fmt="png",
    output_folder=None,
    paths_only=False,
    output_file="page",
    **_kwargs,
):
    document = fitz.open(pdf_path)
    results = []
    scale = dpi / 72.0
    matrix = fitz.Matrix(scale, scale)
    output_dir = Path(output_folder or ".")
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        for page_number, page in enumerate(document, start=1):
            pixmap = page.get_pixmap(matrix=matrix, alpha=False)
            output_path = output_dir / f"{output_file}0001-{page_number:02d}.{fmt}"
            pixmap.save(str(output_path))
            if paths_only:
                results.append(str(output_path))
            else:
                results.append(Image.open(output_path).copy())
        return results
    finally:
        document.close()
