import asyncio
import base64
import unittest
from io import BytesIO
from unittest.mock import AsyncMock, patch
from zipfile import ZipFile

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import ocr
from app.models.user import User


PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=")


class ImageDocumentUploadTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(ocr.router, prefix="/ocr")
        app.dependency_overrides[ocr.require_current_user] = lambda: User(id="user", name="test", email="test@example.com")
        self.client = TestClient(app)
        self.contents = [PNG + bytes([index]) for index in range(3)]
        self.files = [("files", (f"page-{index}.png", content, "image/png")) for index, content in enumerate(self.contents, 1)]
        self.ocr_calls = []

        async def respond(*args, **kwargs):
            self.ocr_calls.append(kwargs)
            number = len(self.ocr_calls)
            return httpx.Response(200, request=httpx.Request("POST", "http://ocr/upload"), json={
                "filename": kwargs["files"]["file"][0], "content_type": "image",
                "pages": [{"page": 1, "text": f"Page {number} original OCR text", "items": [
                    {"text": f"Page {number}", "confidence": 0.95, "bbox": [[10, 10], [60, 30]]}
                ], "tables": [{"bbox": [[1, 1], [70, 50]], "confidence": 0.9, "rows": [["A", "B"]]}]}],
            })

        self.remote = AsyncMock()
        self.remote.__aenter__.return_value = self.remote
        self.remote.post.side_effect = respond
        self.addCleanup(patch.stopall)
        patch.object(ocr.httpx, "AsyncClient", return_value=self.remote).start()
        self.save = patch.object(ocr.supabase_service, "save_ocr_document", return_value={"id": "one-document"}).start()

    def test_three_images_save_one_document_with_ordered_ocr_and_original_images(self):
        response = self.client.post("/ocr/upload-images", files=self.files)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["document_id"], "one-document")
        self.save.assert_called_once()
        saved = self.save.call_args.kwargs
        self.assertEqual(saved["upload_origin"], "RAG")
        self.assertEqual(saved["mime_type"], "application/zip")
        self.assertTrue(saved["filename"].endswith(".images.zip"))
        self.assertEqual([page["page"] for page in saved["pages"]], [1, 2, 3])
        for index, page in enumerate(saved["pages"], 1):
            self.assertEqual(page["text"], f"Page {index} original OCR text")
            self.assertEqual(page["items"][0]["bbox"], [[10, 10], [60, 30]])
            self.assertEqual(page["tables"][0]["rows"], [["A", "B"]])
        self.assertEqual([call["files"]["file"][1] for call in self.ocr_calls], self.contents)
        self.assertTrue(all(call["params"] == {"processing_mode": "document"} for call in self.ocr_calls))
        with ZipFile(BytesIO(saved["content"])) as archive:
            self.assertEqual(archive.namelist(), ["pages/1.png", "pages/2.png", "pages/3.png"])
            for page, original in zip(saved["pages"], self.contents):
                self.assertEqual(archive.read(page["image_file"]), original)

        document = {"id": "one-document", "file_name": saved["filename"], "file_url": "stored-bundle", "bounding_boxes": saved["pages"]}
        with patch.object(ocr.supabase_service, "get_ocr_document", return_value=document) as get_document, patch.object(ocr.supabase_service, "download_document", return_value=(saved["content"], "application/zip")):
            metadata = self.client.get("/ocr/documents/one-document").json()
            self.assertEqual(metadata["content_type"], "image_bundle")
            self.assertEqual(len(metadata["pages"]), 3)
            for page_number, original in enumerate(self.contents, 1):
                result = self.client.get(f"/ocr/documents/one-document/pages/{page_number}/file")
                self.assertEqual(result.status_code, 200)
                self.assertEqual(result.content, original)
                self.assertEqual(result.headers["content-type"], "image/png")
            self.assertEqual(self.client.get("/ocr/documents/one-document/pages/4/file").status_code, 404)
            get_document.assert_called_with("test@example.com", "one-document")

        # Exercise the unchanged indexing service with only storage/embedding I/O mocked.
        from app.services import rag_service
        with patch.object(rag_service.supabase_service, "get_ocr_document", return_value=document), patch.object(rag_service, "embed_texts", new_callable=AsyncMock, return_value=[]) as embed, patch.object(rag_service.supabase_service, "replace_rag_index", return_value={"id": "one-rag-document"}) as replace:
            indexed = asyncio.run(rag_service.index_document("test@example.com", "one-document"))
            self.assertEqual(indexed["id"], "one-rag-document")
            replace.assert_called_once()
            self.assertEqual(replace.call_args.kwargs["document"]["id"], "one-document")
            self.assertEqual({chunk["page_number"] for chunk in replace.call_args.kwargs["chunks"]}, {1, 2, 3})
            embed.assert_awaited_once()

    def test_pdf_and_single_image_keep_original_upload_and_storage(self):
        for filename, content, mime in [("single.png", self.contents[0], "image/png"), ("original.pdf", b"%PDF-original", "application/pdf")]:
            with self.subTest(filename=filename):
                self.save.reset_mock()
                response = self.client.post("/ocr/upload?upload_origin=RAG", files={"file": (filename, content, mime)})
                self.assertEqual(response.status_code, 200, response.text)
                self.save.assert_called_once()
                self.assertEqual(self.save.call_args.kwargs["content"], content)
                self.assertEqual(self.save.call_args.kwargs["filename"], filename)
                self.assertEqual(self.save.call_args.kwargs["mime_type"], mime)

    def test_invalid_batch_is_rejected_before_ocr_and_save(self):
        for files in [self.files[:1], self.files[:1] + [("files", ("bad.pdf", b"bad", "application/pdf"))]]:
            with self.subTest(files=[file[1][0] for file in files]):
                response = self.client.post("/ocr/upload-images", files=files)
                self.assertEqual(response.status_code, 400, response.text)
        self.remote.post.assert_not_awaited()
        self.save.assert_not_called()

    def test_size_limit_applies_to_entire_batch(self):
        with patch.object(ocr, "MAX_FILE_SIZE", len(self.contents[0])):
            response = self.client.post("/ocr/upload-images", files=self.files)
        self.assertEqual(response.status_code, 413)
        self.save.assert_not_called()

    def test_ocr_failure_does_not_save_partial_document(self):
        success = httpx.Response(200, request=httpx.Request("POST", "http://ocr/upload"), json={"filename": "a.png", "content_type": "image", "pages": [{"page": 1, "text": "ok", "items": []}]})
        failure = httpx.Response(500, request=httpx.Request("POST", "http://ocr/upload"), text="OCR failed")
        self.remote.post.side_effect = [success, failure]
        response = self.client.post("/ocr/upload-images", files=self.files)
        self.assertEqual(response.status_code, 502)
        self.save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
