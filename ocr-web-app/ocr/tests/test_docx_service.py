import subprocess
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

pdf_service = types.ModuleType("app.services.pdf_service")
pdf_service.extract_pdf_text = lambda _path: []
pdf_service.extract_pdf_text_and_images = lambda _path, _runner: []
sys.modules.setdefault("app.services.pdf_service", pdf_service)

from app.services.docx_service import (  # noqa: E402
    convert_docx_to_pdf,
    extract_docx_text,
    find_libreoffice,
)


class DocxServiceTests(unittest.TestCase):
    @patch("app.services.docx_service.find_libreoffice", return_value="/usr/bin/libreoffice")
    @patch("app.services.docx_service.subprocess.run")
    @patch("app.services.docx_service.Path.is_file", return_value=True)
    @patch("app.services.docx_service.Path.mkdir")
    def test_convert_docx_to_pdf_uses_headless_libreoffice(
        self,
        _mkdir_mock,
        _is_file_mock,
        run_mock,
        _find_mock,
    ):
        run_mock.return_value = subprocess.CompletedProcess([], 0, "", "")
        output_dir = Path("test-output")
        source = Path("sample.docx")
        result = convert_docx_to_pdf(source, output_dir)

        self.assertEqual(result.name, "sample.pdf")
        command = run_mock.call_args.args[0]
        self.assertIn("--headless", command)
        self.assertIn("pdf:writer_pdf_Export", command)
        self.assertTrue(any(arg.startswith("-env:UserInstallation=") for arg in command))

    @patch("app.services.docx_service.find_libreoffice", return_value=None)
    def test_convert_docx_to_pdf_requires_libreoffice(self, _find_mock):
        with self.assertRaisesRegex(RuntimeError, "LibreOffice"):
            convert_docx_to_pdf(Path("sample.docx"), Path("test-output"))

    @patch.dict(
        "app.services.docx_service.os.environ",
        {"LIBREOFFICE_BIN": "C:/LibreOffice/program/soffice.exe"},
        clear=True,
    )
    @patch("app.services.docx_service.Path.is_file", return_value=True)
    def test_find_libreoffice_accepts_configured_windows_path(self, _is_file_mock):
        self.assertEqual(
            find_libreoffice(),
            "C:\\LibreOffice\\program\\soffice.com",
        )

    @patch("app.services.docx_service.TemporaryDirectory")
    @patch("app.services.docx_service.extract_pdf_text")
    @patch("app.services.docx_service.convert_docx_to_pdf")
    def test_extract_docx_text_processes_converted_pdf(
        self,
        convert_mock,
        extract_mock,
        temp_dir_mock,
    ):
        temp_dir_mock.return_value.__enter__.return_value = "test-output"
        convert_mock.return_value = Path("converted.pdf")
        expected_pages = [object(), object()]
        extract_mock.return_value = expected_pages

        result = extract_docx_text(Path("sample.docx"))

        self.assertIs(result, expected_pages)
        extract_mock.assert_called_once_with(Path("converted.pdf"))


if __name__ == "__main__":
    unittest.main()
