from enum import Enum
from pathlib import Path

import fitz


class FileContentType(str, Enum):
    TEXT_ONLY = "text_only"
    IMAGE_ONLY = "image_only"
    TEXT_AND_IMAGE = "text_and_image"
    UNKNOWN = "unknown"


IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".bmp",
    ".tif",
    ".tiff",
}


TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".csv",
}


def classify_file(file_path: Path) -> FileContentType:
    suffix = file_path.suffix.lower()

    # 이미지 파일
    if suffix in IMAGE_EXTENSIONS:
        return FileContentType.IMAGE_ONLY

    # 일반 텍스트 파일
    if suffix in TEXT_EXTENSIONS:
        return FileContentType.TEXT_ONLY

    # PDF
    if suffix == ".pdf":
        return classify_pdf(file_path)

    return FileContentType.UNKNOWN


def classify_pdf(file_path: Path) -> FileContentType:
    has_text = False
    has_image = False

    document = fitz.open(file_path)

    try:
        for page in document:
            # PDF 내부에 실제 텍스트 객체가 있는지 확인
            text = page.get_text("text")

            if text and text.strip():
                has_text = True

            # PDF 내부에 이미지 객체가 있는지 확인
            images = page.get_images(full=True)

            if images:
                has_image = True

            # 둘 다 발견되면 바로 종료
            if has_text and has_image:
                return FileContentType.TEXT_AND_IMAGE

        if has_text:
            return FileContentType.TEXT_ONLY

        if has_image:
            return FileContentType.IMAGE_ONLY

        return FileContentType.UNKNOWN

    finally:
        document.close()