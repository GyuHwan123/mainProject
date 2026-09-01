from __future__ import annotations

from app.services.finance_evaluation_scoring import *

def verify_workbook(record: dict[str, Any]) -> dict[str, Any]:
    try:
        workbook = load_workbook(BytesIO(build_finance_workbook([record])), data_only=False)
        expected_sheet = SHEET_NAMES.get(record.get("document_type"))
        sheet = workbook[expected_sheet] if expected_sheet in workbook.sheetnames else workbook.active
        column_count = len(HEADERS_BY_TYPE.get(record.get("document_type"), [])) or 8
        headers = [sheet.cell(11, column).value for column in range(1, column_count + 1)]
        rows = [
            [sheet.cell(row, column).value for column in range(1, column_count + 1)]
            for row in range(12, max(12, sheet.max_row))
        ]
        sheet_previews = {}
        for worksheet in workbook.worksheets:
            if worksheet.title == SUMMARY_SHEET_NAME:
                preview_headers = [worksheet.cell(1, column).value for column in range(1, worksheet.max_column + 1)]
                preview_rows = [
                    [worksheet.cell(row, column).value for column in range(1, worksheet.max_column + 1)]
                    for row in range(2, worksheet.max_row + 1)
                ]
            else:
                preview_headers = [worksheet.cell(11, column).value for column in range(1, worksheet.max_column + 1)]
                preview_rows = [
                    [worksheet.cell(row, column).value for column in range(1, worksheet.max_column + 1)]
                    for row in range(12, max(12, worksheet.max_row))
                ]
            sheet_previews[worksheet.title] = {"headers": preview_headers, "rows": preview_rows}
        return {
            "success": expected_sheet in workbook.sheetnames and workbook.active.title == expected_sheet,
            "active_sheet": workbook.active.title,
            "expected_sheet": expected_sheet,
            "preview": {"headers": headers, "rows": rows},
            "sheet_previews": sheet_previews,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}

__all__ = [name for name in globals() if not name.startswith("__")]
