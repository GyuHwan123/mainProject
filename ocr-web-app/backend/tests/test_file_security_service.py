import io
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

import pytest
from fastapi import HTTPException

from app.services.file_security_service import validate_uploaded_file


def office_file(entries: dict[str, bytes], compression: int = ZIP_STORED) -> bytes:
    output = io.BytesIO()
    with ZipFile(output, "w", compression=compression) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return output.getvalue()


def test_accepts_valid_minimal_docx_container():
    content = office_file({"[Content_Types].xml": b"<Types/>", "word/document.xml": b"<document/>"})
    validate_uploaded_file("safe.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", content)


def test_rejects_mime_disguised_file():
    with pytest.raises(HTTPException, match="MIME"):
        validate_uploaded_file("receipt.png", "application/pdf", b"%PDF-1.7\n%%EOF")


def test_rejects_extension_and_signature_mismatch():
    with pytest.raises(HTTPException, match="실제 파일 형식"):
        validate_uploaded_file("receipt.pdf", "application/pdf", b"not a pdf")


def test_rejects_zip_path_traversal():
    content = office_file({"[Content_Types].xml": b"<Types/>", "word/document.xml": b"<document/>", "../evil.exe": b"MZ"})
    with pytest.raises(HTTPException, match="안전하지 않은 경로"):
        validate_uploaded_file("unsafe.docx", None, content)


def test_rejects_high_compression_ratio():
    content = office_file({"[Content_Types].xml": b"<Types/>", "word/document.xml": b"0" * 1_000_000}, ZIP_DEFLATED)
    with pytest.raises(HTTPException, match="압축 폭탄"):
        validate_uploaded_file("bomb.docx", None, content)


def test_rejects_macro_or_embedded_executable():
    content = office_file({"[Content_Types].xml": b"<Types/>", "word/document.xml": b"<document/>", "word/vbaProject.bin": b"macro"})
    with pytest.raises(HTTPException, match="매크로"):
        validate_uploaded_file("macro.docx", None, content)


def test_rejects_eicar_test_signature():
    eicar = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
    with pytest.raises(HTTPException, match="악성"):
        validate_uploaded_file("sample.txt", "text/plain", eicar)
