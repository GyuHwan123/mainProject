"""Offline scope, mapping, and recovery tests; no network or DB calls."""
from copy import deepcopy
import unittest

import replace_supabase_v3 as sync


class FakeAPI:
    def __init__(self):
        self.documents = [{"id": f"doc-{i}", "doc_id": name} for i, name in enumerate(sync.canonical_ids())]
        self.documents.append({"id": "personal-doc", "doc_id": "personal"})
        self.chunks = [{"id": "old-row", "document_id": "doc-0", "chunk_index": 0, "content": "original", "embedding": "[1,0]"},
                       {"id": "personal-row", "document_id": "personal-doc", "content": "private"}]
        self.deletions = []

    def rows(self, table):
        return deepcopy(sorted(self.documents if table == "rag_documents" else self.chunks, key=lambda x: x["id"]))

    def request(self, method, table, params, payload=None):
        if table != "rag_chunks":
            raise AssertionError("Out-of-scope write")
        if method == "DELETE":
            ids = set(params["id"][4:-1].split(","))
            docs = set(params["document_id"][4:-1].split(","))
            self.deletions.append((ids, docs))
            removed = [r for r in self.chunks if r["id"] in ids and r["document_id"] in docs]
            self.chunks = [r for r in self.chunks if r not in removed]
            return removed
        if method == "POST":
            self.chunks.extend(deepcopy(payload))
            return payload
        raise AssertionError("Unexpected method")


class ReplacementTests(unittest.TestCase):
    def test_recovery_removes_only_planned_ids_and_restores_full_original_rows(self):
        api = FakeAPI()
        before = sync.state(api)
        planned = [{"id": "new-row", "document_id": "doc-0", "chunk_index": 0, "content": "v3"}]
        api.chunks = [r for r in api.chunks if r["id"] != "old-row"] + deepcopy(planned)
        sync.restore(api, before, planned)
        self.assertEqual(sync.state(api), before)
        self.assertEqual(api.deletions[0][0], {"new-row"})
        self.assertNotIn("personal-doc", api.deletions[0][1])

    def test_recovery_refuses_to_touch_unknown_concurrent_company_row(self):
        api = FakeAPI()
        before = sync.state(api)
        api.chunks.append({"id": "unknown-row", "document_id": "doc-0", "chunk_index": 99})
        with self.assertRaises(RuntimeError):
            sync.restore(api, before, [])
        self.assertEqual(api.deletions, [])

    def test_mapping_and_supported_payload_columns(self):
        chunks, embeddings, _ = sync.local_inputs()
        mapping = {name: f"doc-{i}" for i, name in enumerate(sync.canonical_ids())}
        fields = ["id", "document_id", "chunk_index", "page_number", "content", "embedding", "document_title", "section_path", "bbox", "section_title"]
        schema = {"properties": {k: {} for k in fields}}
        schema["properties"]["embedding"]["format"] = "public.vector(1024)"
        rows = sync.payload_rows(chunks, embeddings, mapping, schema)
        self.assertEqual(len(rows), 115)
        self.assertEqual(rows[3]["document_id"], mapping["HR-001"])
        self.assertEqual(rows[38]["document_id"], mapping["GA-001"])
        self.assertEqual(rows[38]["content"], chunks[38]["text"])
        self.assertNotIn("source_spans", rows[38])
        bad = dict(mapping)
        bad.pop("HR-001")
        with self.assertRaises(ValueError):
            sync.payload_rows(chunks, embeddings, bad, schema)

    def test_deletion_requires_both_ids_and_full_company_mapping(self):
        with self.assertRaises(ValueError):
            sync.delete_exact(FakeAPI(), [], ["doc-0"])


if __name__ == "__main__":
    unittest.main()
