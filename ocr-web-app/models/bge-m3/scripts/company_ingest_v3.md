# Company chunking v3

This command only generates local chunks. It has no model loading, embedding
generation, Supabase calls, or credentials dependency. It does not import the
legacy ingestion entry points, whose imports or top-level code load models.

From `ocr-web-app`, using a Python environment with the two dependencies in
`models/bge-m3/scripts/requirements-company-v3.txt`:

```powershell
python models/bge-m3/scripts/company_ingest_v3.py
python -m unittest discover -s models/bge-m3/scripts -p test_company_ingest_v3.py -v
```

Defaults: target 380, maximum 600 characters **including** the document, section,
article, and table header context. Short articles/tables are kept whole even when
they exceed the target. Long articles split at items, paragraphs, sentences, then
characters. No overlap is used. Different articles never fill each other's size
budget. Text before/after a table remains separate, with the same article context.

## Extraction and source accounting

- PyMuPDF `find_tables()` identifies tables. Characters inside each table's bbox
  belong to that table; all other characters remain in ordered text line blocks.
- The legacy `_clean_cell()` and `[표 행] header: value` serialization behavior
  is preserved and tested against the actual legacy function definitions.
- Blocks are sorted by page, top coordinate, then left coordinate. This is
  intended for the 18 native, single-column company PDFs, not arbitrary magazines.
- The pypdf and PyMuPDF non-whitespace character multisets must match per page.
  Spatial block assignment must account for every character. Table cell text must
  account for the characters assigned to each table.
- Every retained text block has exact character-range coverage in chunk source
  spans. Every table's rendered rows must occur once, intact, in row-index order.
  The final rendered text is also checked against its source spans.
- A chapter resets article context before subsequent text/table blocks. An article
  heading at the end of a page remains attached to its body on the following page.
- Only the exact corpus notice, catalog title/document header, known footer and
  revision-history lines are removed from retrieval text. The source blocks remain
  in the audit; notices, headers, and revision history are retained as metadata.
  Document information tables keep effective dates, owners, security and status.

## Outputs

All generated files live in `data/company_documents/processed_v3/`:

- `company_chunks_v3.json` / `.pkl`: identical ordered chunk dictionaries.
- `company_blocks_v3.json`: ordered source blocks, table cells, bbox, disposition.
- `company_ingest_manifest_v3.json`: lengths, document counts, coverage checks,
  source hashes, chunk fingerprint, previous 37/99 comparison, protected hashes.

Legacy `text`, `doc_id`, `page`, `chunk_index` and catalog fields are retained.
`content` is exactly equal to `text`. Additional fields include `chunk_type`,
`start_page`, `end_page`, `section_title`, `article_title`, `table_id`, `source_spans`
and `document_info`. `bbox` refers to the first source page only; multi-page source
locations are in `source_spans`. Repeated context has explicit `context_block_ids`.
Table row indices are zero-based indices into the serialized data rows, excluding
the header. Block `table_index` is zero-based within its source page.

## Failures and limits

Unaccounted characters, unsupported/headerless tables, overlapping table ownership,
image blocks requiring OCR, orphan headings, empty/duplicate chunks and other
coverage failures stop the run before output generation. No silent fallback to the
old 600-character pipeline is used.

An individual table row that exceeds the maximum is never cut. It remains intact
and is marked `oversized_atomic_row`; the manifest counts these exceptions. This
does not occur in the current 18 PDFs. A context/header that itself exceeds the
maximum is rejected. Multi-page table identity and arbitrary multi-column reading
order are not inferred; those layouts require explicit additional handling.

## Next experiment (not performed here)

Use `processed_v3/company_chunks_v3.pkl`, in stored order, and embed each dictionary's
`text` once with `BAAI/bge-m3`. Do not embed both `text` and its alias `content`.
Store a new variant associated with the v3 fingerprint, not in the existing 37-row
baseline file. The current worker still requires exactly 37 chunks and the old
fingerprint, so it must be adapted in a separate task before v3 embedding/sync.
No DB schema or backend retrieval change is required by this local artifact format.
