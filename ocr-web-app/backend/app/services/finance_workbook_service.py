from __future__ import annotations

from datetime import date
from io import BytesIO
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation


SHEET_NAMES = {
    "EXPENSE_REPORT": "경비지출결의서",
    "TRAVEL_EXPENSE": "출장여비교통비정산서",
    "PURCHASE_REQUEST": "구매품의요청서",
    "WELFARE_BENEFIT": "복리후생비신청서",
}

TITLE_BY_TYPE = {
    "EXPENSE_REPORT": "경 비 지 출 결 의 서",
    "TRAVEL_EXPENSE": "출 장 / 여 비 교 통 비  정 산 서",
    "PURCHASE_REQUEST": "구 매 / 품 의 요 청 서",
    "WELFARE_BENEFIT": "복 리 후 생 비  신 청 서",
}

DETAIL_HEADERS = ["영수증 ID", "품목 순번", "거래일", "상호명", "품목명", "수량", "단가", "품목금액"]
HEADERS_BY_TYPE = {
    kind: DETAIL_HEADERS[:2] + (["지출 카테고리"] if kind in {"TRAVEL_EXPENSE", "WELFARE_BENEFIT"} else []) + DETAIL_HEADERS[2:]
    for kind in SHEET_NAMES
}
SUMMARY_SHEET_NAME = "영수증요약"
SUMMARY_HEADERS = [
    "영수증 ID", "원본 파일명", "문서 유형", "지출 카테고리", "거래일", "상호명", "결제수단",
    "공급가액", "부가세", "할인액", "최종 결제금액", "품목 수", "품목금액 합계", "차이금액", "금액 대조 상태",
]


def _excel_safe(value: Any) -> Any:
    """Keep untrusted text from being interpreted as an Excel formula."""
    if not isinstance(value, str):
        return value
    first = value.lstrip()[:1]
    return "'" + value if first in {"=", "+", "-", "@"} else value


def _number(value: Any) -> float:
    return _optional_number(value) or 0


def _items(record: dict[str, Any]) -> list[dict[str, Any]]:
    value = (record.get("structured_data") or {}).get("items")
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _record_rows(document_type: str, records: list[dict[str, Any]]) -> list[list[Any]]:
    rows = []
    for record in records:
        receipt_id = str(record.get("document_id") or record.get("id") or "미확인")
        # No placeholder item: receipt-only amounts belong in the summary.
        for index, item in enumerate(_items(record), 1):
            row = [receipt_id, index]
            if document_type in {"TRAVEL_EXPENSE", "WELFARE_BENEFIT"}:
                row.append(record.get("expense_category"))
            row.extend([record.get("transaction_date"), record.get("merchant"), item.get("name"),
                        _optional_number(item.get("quantity")), _optional_number(item.get("unit_price")),
                        _optional_number(item.get("total_amount"))])
            rows.append([_excel_safe(value) for value in row])
    return rows


def _document_number(document_type: str, email: str, created_on: str) -> str:
    prefix = {"EXPENSE_REPORT": "EXP", "TRAVEL_EXPENSE": "TRV", "PURCHASE_REQUEST": "PUR", "WELFARE_BENEFIT": "WEL"}[document_type]
    checksum = sum((index + 1) * ord(character) for index, character in enumerate(email.lower())) % 1_000_000
    return f"{prefix}-{created_on.replace('-', '')}-{checksum:06d}"


def _record_period(records: list[dict[str, Any]]) -> str:
    dates = sorted(str(record.get("transaction_date")) for record in records if record.get("transaction_date"))
    if not dates:
        return "미확인"
    return dates[0] if len(dates) == 1 else f"{dates[0]} ~ {dates[-1]}"


def _summary_text(records: list[dict[str, Any]], field: str, fallback: str = "미입력") -> str:
    values = []
    for record in records:
        data = record.get("structured_data") or {}
        value = data.get(field)
        if value and str(value) not in values:
            values.append(str(value))
    return " / ".join(values[:3]) or fallback


def _optional_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _receipt_summary_rows(records: list[dict[str, Any]]) -> list[list[Any]]:
    rows = []
    for record in records:
        data = record.get("structured_data") or {}
        items = _items(record)
        amounts = [_optional_number(item.get("total_amount")) for item in items]
        total = _optional_number(record.get("total_amount"))
        discount = _optional_number(data.get("discount_amount"))
        complete = bool(items) and all(amount is not None for amount in amounts)
        item_total = sum(amounts) if complete else None
        difference = total - item_total if total is not None and item_total is not None else None
        if not items:
            status = "확인 필요: 품목 없음"
        elif not complete:
            status = "대조 불가: 품목금액 미확인"
        elif total is None:
            status = "대조 불가: 결제금액 미확인"
        elif abs(difference) <= 0.01:
            status = "금액 일치"
        elif discount is not None and discount > 0 and abs(difference + discount) <= 0.01:
            status = "할인액과 차이 일치"
        else:
            status = "확인 필요: 금액 차이"
        rows.append([
            str(record.get("document_id") or record.get("id") or "미확인"), data.get("source_filename"),
            SHEET_NAMES.get(record.get("document_type"), record.get("document_type")), record.get("expense_category"),
            record.get("transaction_date"), record.get("merchant"), record.get("payment_method"),
            _optional_number(record.get("supply_amount")), _optional_number(record.get("tax_amount")), discount,
            total, len(items), item_total, difference, status,
        ])
    return rows


def _style_summary_sheet(ws, records: list[dict[str, Any]]) -> None:
    line = Side(style="thin", color="AAB7C4")
    ws.sheet_view.showGridLines = False
    ws.freeze_panes = "G2"
    ws.append(SUMMARY_HEADERS)
    for cell in ws[1]:
        cell.fill = PatternFill("solid", fgColor="1F4E78")
        cell.font = Font(bold=True, color="FFFFFF")
    for values in _receipt_summary_rows(records):
        ws.append([_excel_safe(value) for value in values])
    for row in ws:
        for cell in row:
            cell.border = Border(top=line, bottom=line, left=line, right=line)
            cell.alignment = Alignment(vertical="center", wrap_text=True)
            if cell.row > 1 and 8 <= cell.column <= 14:
                cell.number_format = '#,##0.###'
    ws.row_dimensions[1].height = 34
    for index, width in enumerate([38, 28, 24, 20, 16, 26, 14, 16, 16, 16, 18, 12, 18, 18, 32], 1):
        ws.column_dimensions[get_column_letter(index)].width = width
    ws.auto_filter.ref = f"A1:O{max(len(records) + 1, 1)}"
    for row in range(2, len(records) + 2):
        if ws.cell(row, 14).value is not None:
            ws.cell(row, 14, f"=K{row}-M{row}")
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.orientation = "landscape"
    ws.page_setup.paperSize = ws.PAPERSIZE_A3
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.print_title_rows = "1:1"


def _style_sheet(ws, document_type: str, records: list[dict[str, Any]], author: dict[str, str]) -> None:
    dark = "1F4E78"
    light = "D9EAF7"
    line = Side(style="thin", color="AAB7C4")
    ws.sheet_view.showGridLines = False
    ws.freeze_panes = "A12"
    ws.merge_cells("A2:D3")
    ws["A2"] = TITLE_BY_TYPE[document_type]
    ws["A2"].font = Font(name="맑은 고딕", size=18, bold=True, color="FFFFFF")
    ws["A2"].fill = PatternFill("solid", fgColor=dark)
    ws["A2"].alignment = Alignment(horizontal="center", vertical="center")
    ws.merge_cells("E2:E4")
    ws["E2"] = "결\n\n재"
    ws["E2"].alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for column, label in zip(("F", "G", "H"), ("기안자", "검토자", "승인자")):
        ws.merge_cells(f"{column}3:{column}4")
        ws[f"{column}2"] = label
        ws[f"{column}2"].font = Font(bold=True)
        ws[f"{column}2"].alignment = Alignment(horizontal="center")
    created_on = date.today().isoformat()
    author_name = author.get("name") or author.get("email") or "작성자 미확인"
    author_email = author.get("email") or "미등록"
    department = author.get("department") or "미등록"
    ws["F3"] = author_name

    ws["A5"], ws["B5"], ws["E5"], ws["F5"] = "문서번호", _document_number(document_type, author_email, created_on), "처리상태", "재무팀 확정 대기중"
    ws.merge_cells("B5:D5"); ws.merge_cells("F5:H5")
    finance_status = DataValidation(
        type="list", formula1='"재무팀 확정 대기중,재무팀 확정"',
        allow_blank=False, showDropDown=False, showErrorMessage=True,
        errorStyle="stop", errorTitle="처리상태 선택",
        error="목록에서 재무팀 확정 대기중 또는 재무팀 확정을 선택해 주세요.",
    )
    ws.add_data_validation(finance_status)
    finance_status.add(ws["F5"])

    if document_type == "EXPENSE_REPORT":
        metadata = [("기안일자", created_on, "부서명", department), ("기안자", author_name, "작성자 이메일", author_email), ("정산기간", _record_period(records), "영수증 수", f"{len(records)}건")]
    elif document_type == "TRAVEL_EXPENSE":
        metadata = [("출장자", author_name, "소속부서", department), ("출장목적", _summary_text(records, "note", "영수증 자동 정산"), "작성자 이메일", author_email), ("출장기간", _record_period(records), "출장지", _summary_text(records, "location"))]
    elif document_type == "PURCHASE_REQUEST":
        merchants = " / ".join(dict.fromkeys(str(record.get("merchant")) for record in records if record.get("merchant"))) or "미확인"
        receipt_total = sum(_number(record.get("total_amount")) for record in records)
        metadata = [("거래일", _record_period(records), "거래처", merchants), ("작성부서", department, "작성자", author_name), ("영수증 총액", receipt_total, "비용구분", records[0].get("expense_category", "미입력") if records else "미입력")]
    else:
        metadata = [("신청일자", created_on, "소속부서", department), ("신청인", author_name, "작성자 이메일", author_email), ("신청기간", _record_period(records), "신청건수", f"{len(records)}건")]

    for row_number, (left_label, left_value, right_label, right_value) in enumerate(metadata, 6):
        ws[f"A{row_number}"], ws[f"B{row_number}"], ws[f"E{row_number}"], ws[f"F{row_number}"] = left_label, _excel_safe(left_value), right_label, _excel_safe(right_value)
        ws.merge_cells(f"B{row_number}:D{row_number}"); ws.merge_cells(f"F{row_number}:H{row_number}")
    for row_number in range(5, 9):
        for cell in ws[row_number]:
            cell.fill = PatternFill("solid", fgColor=light if row_number > 5 else "EAF1F7")
            cell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
        ws[f"A{row_number}"].font = Font(bold=True); ws[f"E{row_number}"].font = Font(bold=True)

    header_row = 11
    for column, label in enumerate(HEADERS_BY_TYPE[document_type], 1):
        cell = ws.cell(header_row, column, label)
        cell.fill = PatternFill("solid", fgColor=dark)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = Border(top=line, bottom=line, left=line, right=line)

    rows = _record_rows(document_type, records)
    column_count = len(HEADERS_BY_TYPE[document_type])
    if not rows:
        rows = [[None] * column_count]
    for row_index, values in enumerate(rows, header_row + 1):
        for column, value in enumerate(values, 1):
            cell = ws.cell(row_index, column, value)
            cell.border = Border(top=line, bottom=line, left=line, right=line)
            money_columns = {column_count - 1, column_count}
            cell.alignment = Alignment(horizontal="right" if column in money_columns else "left", vertical="center", wrap_text=True)
            if column in money_columns or column == column_count - 2:
                cell.number_format = '#,##0.###'

    total_row = header_row + len(rows) + 1
    ws.merge_cells(start_row=total_row, start_column=1, end_row=total_row, end_column=column_count - 1)
    ws.cell(total_row, 1, "품목금액 합계 (미확인 금액 제외)")
    ws.cell(total_row, 1).font = Font(bold=True)
    ws.cell(total_row, 1).fill = PatternFill("solid", fgColor=light)
    letter = get_column_letter(column_count)
    amount_range = f"{letter}{header_row + 1}:{letter}{total_row - 1}"
    ws.cell(total_row, column_count, f'=IF(COUNT({amount_range})=0,"",SUM({amount_range}))')
    ws.cell(total_row, column_count).number_format = '#,##0.###" 원"'
    for column in range(1, column_count + 1):
        ws.cell(total_row, column).border = Border(top=Side(style="medium", color=dark))
    widths = [38, 10] + ([20] if column_count == 9 else []) + [16, 26, 30, 12, 16, 18]
    for index, width in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(index)].width = width
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.orientation = "landscape"
    ws.page_setup.paperSize = ws.PAPERSIZE_A3
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.print_title_rows = "11:11"
    ws.row_dimensions[2].height = 30
    ws.row_dimensions[11].height = 34
    ws.auto_filter.ref = f"A11:{get_column_letter(column_count)}{max(total_row - 1, 12)}"


def build_finance_workbook(records: list[dict[str, Any]], author: dict[str, str] | None = None) -> bytes:
    workbook = Workbook()
    workbook.remove(workbook.active)
    workbook.calculation.fullCalcOnLoad = True
    workbook.calculation.forceFullCalc = True
    workbook.calculation.calcMode = "auto"
    for document_type, sheet_name in SHEET_NAMES.items():
        ws = workbook.create_sheet(sheet_name)
        matching = [record for record in records if record.get("document_type") == document_type]
        _style_sheet(ws, document_type, matching, author or {})
    summary_sheet = workbook.create_sheet(SUMMARY_SHEET_NAME)
    _style_summary_sheet(summary_sheet, records)
    first_document_type = records[0].get("document_type") if records else None
    if first_document_type in SHEET_NAMES:
        workbook.active = list(SHEET_NAMES).index(first_document_type)
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()
