import os
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Callable

from app.schemas.ocr import OCRPage
from app.services.pdf_service import extract_pdf_text, extract_pdf_text_and_images


DOCX_CONVERSION_TIMEOUT_SECONDS = 120


def find_libreoffice() -> str | None:
    """Find LibreOffice on PATH or in its standard Windows locations."""
    configured = os.getenv("LIBREOFFICE_BIN")
    if configured:
        configured_path = Path(configured).expanduser()
        if configured_path.is_file():
            console_path = configured_path.with_suffix(".com")
            return str(console_path if os.name == "nt" and console_path.is_file() else configured_path)
        executable = shutil.which(configured)
        if executable:
            return executable

    for command in ("libreoffice", "soffice"):
        executable = shutil.which(command)
        if executable:
            return executable

    if os.name == "nt":
        windows_candidates = [
            Path(os.environ.get("PROGRAMFILES", ""))
            / "LibreOffice"
            / "program"
            / "soffice.com",
            Path(os.environ.get("PROGRAMFILES(X86)", ""))
            / "LibreOffice"
            / "program"
            / "soffice.com",
            Path(os.environ.get("LOCALAPPDATA", ""))
            / "Programs"
            / "LibreOffice"
            / "program"
            / "soffice.com",
        ]
        for candidate in windows_candidates:
            if candidate.is_file():
                return str(candidate)

    return None


def convert_docx_to_pdf(file_path: Path, output_dir: Path) -> Path:
    """Convert a DOCX to PDF with LibreOffice in an isolated user profile."""
    executable = find_libreoffice()
    if executable is None:
        raise RuntimeError(
            "LibreOffice를 찾을 수 없습니다. DOCX 처리를 위해 LibreOffice를 "
            "설치하거나 LIBREOFFICE_BIN에 soffice 실행 파일 경로를 지정해 주세요."
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    profile_dir = output_dir / "libreoffice-profile"

    try:
        completed = subprocess.run(
            [
                executable,
                "--headless",
                "--nologo",
                "--nodefault",
                "--nolockcheck",
                "--nofirststartwizard",
                "--norestore",
                f"-env:UserInstallation={profile_dir.resolve().as_uri()}",
                "--convert-to",
                "pdf:writer_pdf_Export",
                "--outdir",
                str(output_dir),
                str(file_path),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=DOCX_CONVERSION_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("DOCX to PDF conversion timed out.") from exc

    pdf_path = output_dir / f"{file_path.stem}.pdf"
    if completed.returncode != 0 or not pdf_path.is_file():
        details = (completed.stderr or completed.stdout).strip()
        message = "LibreOffice failed to convert the DOCX file to PDF."
        if details:
            message = f"{message} {details}"
        raise RuntimeError(message)

    return pdf_path


def _process_converted_docx(
    file_path: Path,
    pdf_processor: Callable[[Path], list[OCRPage]],
) -> list[OCRPage]:
    with TemporaryDirectory(prefix="docx-conversion-") as temp_dir:
        pdf_path = convert_docx_to_pdf(file_path, Path(temp_dir))
        return pdf_processor(pdf_path)


def extract_docx_text(file_path: Path) -> list[OCRPage]:
    """Extract DOCX text page by page through a PDF conversion."""
    return _process_converted_docx(file_path, extract_pdf_text)


def extract_docx_text_and_images(
    file_path: Path,
    ocr_runner: Callable[[Path], list[OCRPage]],
) -> list[OCRPage]:
    """Preserve DOCX page layout while extracting native text and image OCR."""
    return _process_converted_docx(
        file_path,
        lambda pdf_path: extract_pdf_text_and_images(pdf_path, ocr_runner),
    )


def convert_docx_to_pdf_bytes(file_path: Path) -> bytes:
    """Convert a DOCX to PDF and return the PDF for browser preview."""
    with TemporaryDirectory(prefix="docx-preview-") as temp_dir:
        pdf_path = convert_docx_to_pdf(file_path, Path(temp_dir))
        return pdf_path.read_bytes()
