from __future__ import annotations

import io
import shutil
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from zipfile import BadZipFile, ZipFile

from fastapi import HTTPException


MAX_FILE_SIZE = 50 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 10_000
MAX_ARCHIVE_UNCOMPRESSED = 250 * 1024 * 1024
MAX_ARCHIVE_MEMBER_SIZE = 100 * 1024 * 1024
MAX_COMPRESSION_RATIO = 100

ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".docx", ".xlsx", ".xlsm", ".txt", ".md", ".csv"}
ZIP_EXTENSIONS = {".docx", ".xlsx", ".xlsm"}
DANGEROUS_MEMBER_EXTENSIONS = {".exe", ".dll", ".com", ".scr", ".bat", ".cmd", ".ps1", ".js", ".jse", ".vbs", ".vbe", ".msi", ".jar", ".lnk"}
MIME_BY_EXTENSION = {
    ".pdf": {"application/pdf"},
    ".png": {"image/png"},
    ".jpg": {"image/jpeg"}, ".jpeg": {"image/jpeg"},
    ".webp": {"image/webp"}, ".bmp": {"image/bmp", "image/x-ms-bmp"},
    ".tif": {"image/tiff"}, ".tiff": {"image/tiff"},
    ".docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document", "application/zip"},
    ".xlsx": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/zip"},
    ".xlsm": {"application/vnd.ms-excel.sheet.macroenabled.12", "application/zip"},
    ".txt": {"text/plain"}, ".md": {"text/plain", "text/markdown"},
    ".csv": {"text/plain", "text/csv", "application/csv", "application/vnd.ms-excel"},
}


def _reject(detail: str, status_code: int = 400) -> None:
    raise HTTPException(status_code=status_code, detail=detail)


def _check_signature(extension: str, content: bytes) -> None:
    valid = {
        ".pdf": content.startswith(b"%PDF-") and b"%%EOF" in content[-4096:],
        ".png": content.startswith(b"\x89PNG\r\n\x1a\n") and b"IEND" in content[-32:],
        ".jpg": content.startswith(b"\xff\xd8\xff") and content.rstrip().endswith(b"\xff\xd9"),
        ".jpeg": content.startswith(b"\xff\xd8\xff") and content.rstrip().endswith(b"\xff\xd9"),
        ".webp": len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP",
        ".bmp": content.startswith(b"BM"),
        ".tif": content.startswith((b"II*\x00", b"MM\x00*")),
        ".tiff": content.startswith((b"II*\x00", b"MM\x00*")),
    }.get(extension)
    if valid is False:
        _reject("파일 확장자와 실제 파일 형식이 다르거나 파일이 손상되었습니다.")


def _check_zip_document(extension: str, content: bytes) -> None:
    try:
        with ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ARCHIVE_ENTRIES:
                _reject("압축 문서의 파일 수가 안전 제한을 초과했습니다.")
            total_size = 0
            names = set()
            for entry in entries:
                path = PurePosixPath(entry.filename.replace("\\", "/"))
                if path.is_absolute() or ".." in path.parts:
                    _reject("압축 문서에 안전하지 않은 경로가 포함되어 있습니다.")
                if entry.flag_bits & 0x1:
                    _reject("암호화된 압축 문서는 업로드할 수 없습니다.")
                if (entry.external_attr >> 16) & 0o170000 == 0o120000:
                    _reject("압축 문서에 심볼릭 링크가 포함되어 있습니다.")
                if Path(entry.filename).suffix.lower() in DANGEROUS_MEMBER_EXTENSIONS:
                    _reject("문서 내부에 실행 가능한 파일이 포함되어 있습니다.")
                lower_name = entry.filename.lower()
                if "vbaproject.bin" in lower_name or lower_name.endswith(".bin") and "embeddings/" in lower_name:
                    _reject("매크로 또는 실행 가능한 내장 개체가 포함된 문서는 업로드할 수 없습니다.")
                if entry.file_size > MAX_ARCHIVE_MEMBER_SIZE:
                    _reject("압축 문서 내부 파일이 안전 제한을 초과했습니다.")
                total_size += entry.file_size
                if total_size > MAX_ARCHIVE_UNCOMPRESSED:
                    _reject("압축 해제 크기가 안전 제한을 초과했습니다. 압축 폭탄이 의심됩니다.")
                if entry.file_size and entry.file_size / max(1, entry.compress_size) > MAX_COMPRESSION_RATIO:
                    _reject("비정상적으로 높은 압축률이 감지되었습니다. 압축 폭탄이 의심됩니다.")
                names.add(lower_name)
            required = "word/document.xml" if extension == ".docx" else "xl/workbook.xml"
            if "[content_types].xml" not in names or required not in names:
                _reject("파일 확장자와 실제 Office 문서 형식이 다르거나 파일이 손상되었습니다.")
            if archive.testzip() is not None:
                _reject("압축 문서가 손상되었습니다.")
    except BadZipFile:
        _reject("Office 문서가 손상되었거나 올바른 ZIP 문서가 아닙니다.")


def _check_malware(content: bytes, filename: str) -> None:
    if b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*" in content:
        _reject("악성 파일이 감지되었습니다.")
    scanner = shutil.which("clamscan")
    if not scanner:
        return
    suffix = Path(filename).suffix
    with tempfile.NamedTemporaryFile(suffix=suffix) as temp:
        temp.write(content); temp.flush()
        try:
            result = subprocess.run([scanner, "--no-summary", temp.name], capture_output=True, timeout=60, check=False)
        except subprocess.TimeoutExpired:
            _reject("악성코드 검사가 시간 제한을 초과했습니다.", 503)
    if result.returncode == 1:
        _reject("악성 파일이 감지되었습니다.")
    if result.returncode > 1:
        _reject("악성코드 검사를 완료하지 못했습니다.", 503)


def validate_uploaded_file(filename: str, content_type: str | None, content: bytes) -> None:
    extension = Path(filename or "").suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        _reject("지원하지 않는 파일 형식입니다.")
    if not content:
        _reject("빈 파일은 업로드할 수 없습니다.")
    if len(content) > MAX_FILE_SIZE:
        _reject("파일은 최대 50MB까지 업로드할 수 있습니다.", 413)
    declared_mime = (content_type or "").split(";", 1)[0].strip().lower()
    if declared_mime and declared_mime != "application/octet-stream" and declared_mime not in MIME_BY_EXTENSION[extension]:
        _reject("파일 확장자와 MIME 형식이 일치하지 않습니다.")
    _check_signature(extension, content)
    if extension in ZIP_EXTENSIONS:
        _check_zip_document(extension, content)
    elif extension in {".txt", ".md", ".csv"} and b"\x00" in content:
        _reject("텍스트 파일에 허용되지 않는 바이너리 데이터가 포함되어 있습니다.")
    _check_malware(content, filename)
