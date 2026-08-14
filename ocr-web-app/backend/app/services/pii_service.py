from __future__ import annotations

import re
from typing import Any

MASK = "[민감정보 보호]"
PRIVACY_RESPONSE = "요청하신 정보는 개인정보 보호 정책에 따라 제공할 수 없습니다. 채용 검토에 필요한 학력, 경력, 기술, 교육 및 자격 정보는 질문할 수 있습니다."
SENSITIVE_QUERY = re.compile(
    r"(생년월일|생년|몇\s*살|나이|연령|성별|남자인지|여자인지|휴대폰|핸드폰|전화번호|연락처|"
    r"이메일|e-mail|메일주소|주소|거주지|어디\s*(?:에\s*)?살|사는\s*곳|주민등록|주민번호|계좌번호|통장번호)",
    re.I,
)
PATTERNS = [
    re.compile(r"\b\d{6}\s*[- ]?\s*[1-4]\d{6}\b"),
    re.compile(r"\b01[016789][ -]?\d{3,4}[ -]?\d{4}\b"),
    re.compile(r"\b0\d{1,2}[ -]?\d{3,4}[ -]?\d{4}\b"),
    re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}"),
    re.compile(r"\b\d{2,4}[- ]\d{2,4}[- ]\d{3,6}\b"),
]
SENSITIVE_LABEL = re.compile(r"(생년월일|생년|나이|성별|주소|거주지|연락처|휴대폰|핸드폰|전화|이메일|e-mail|계좌|주민등록)", re.I)
FIELD_LABEL = re.compile(
    r"^(성명|이름|생년월일|생년|나이|성별|주소|거주지|연락처|휴대폰|핸드폰|전화|이메일|e-mail|계좌|주민등록|"
    r"경력구분|홈페이지|학력사항|경력사항|교육/연수|수상내용|자격증|재학기간|학교명(?:\s*및\s*전공)?|졸업상태|"
    r"근무기간|근무회사|부서|직위|담당직무|기간|과정명|기관|취득일자|자격증/면허증|발급처|비고)\s*[:：]?$",
    re.I,
)
ADDRESS = re.compile(r"(?:서울|부산|대구|인천|광주|대전|울산|세종|경기|강원|충북|충남|전북|전남|경북|경남|제주)[^\n|]{2,50}(?:시|군|구|동|읍|면|로|길)")


def is_sensitive_query(query: str) -> bool:
    return bool(SENSITIVE_QUERY.search(str(query or "")))


def _rect(item: dict[str, Any]) -> tuple[float, float, float, float] | None:
    points = [point for point in (item.get("bbox") or []) if len(point) >= 2]
    if not points:
        return None
    xs, ys = [float(p[0]) for p in points], [float(p[1]) for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def _rows(items: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    positioned = [(item, _rect(item)) for item in items if str(item.get("text", "")).strip()]
    positioned.sort(key=lambda value: ((value[1] or (0, 10**9, 0, 0))[1], (value[1] or (10**9, 0, 0, 0))[0]))
    rows: list[dict[str, Any]] = []
    for item, rect in positioned:
        if rect is None:
            rows.append({"center": 10**9 + len(rows), "height": 1, "items": [item]})
            continue
        center, height = (rect[1] + rect[3]) / 2, max(1, rect[3] - rect[1])
        row = next((candidate for candidate in reversed(rows[-4:]) if abs(candidate["center"] - center) <= max(5, min(candidate["height"], height) * .7)), None)
        if row is None:
            rows.append({"center": center, "height": height, "items": [item]})
        else:
            row["items"].append(item)
    for row in rows:
        row["items"].sort(key=lambda item: (_rect(item) or (10**9, 0, 0, 0))[0])
    return [row["items"] for row in rows]


def _direct_pii(text: str) -> bool:
    return bool(ADDRESS.search(text) or any(pattern.search(text) for pattern in PATTERNS))


def _sensitive_indexes(row: list[dict[str, Any]]) -> set[int]:
    indexes = {index for index, item in enumerate(row) if _direct_pii(str(item.get("text", "")))}
    for index, item in enumerate(row):
        text = str(item.get("text", "")).strip()
        if not SENSITIVE_LABEL.search(text):
            continue
        label_only = bool(FIELD_LABEL.fullmatch(text))
        # 라벨과 값이 한 bbox에 들어 있으면 해당 bbox 전체를 보호합니다.
        if not label_only:
            indexes.add(index)
        # 표에서는 다음 필드 라벨 전까지 여러 셀로 쪼개진 값을 모두 보호합니다.
        for value_index in range(index + 1, len(row)):
            value_text = str(row[value_index].get("text", "")).strip()
            if FIELD_LABEL.fullmatch(value_text):
                break
            indexes.add(value_index)
    return indexes


def _merge_row_boxes(row: list[dict[str, Any]], indexes: set[int]) -> list[list[list[float]]]:
    groups: list[list[int]] = []
    for index in sorted(indexes):
        if groups and index == groups[-1][-1] + 1:
            groups[-1].append(index)
        else:
            groups.append([index])
    merged = []
    for group in groups:
        points = [point for index in group for point in (row[index].get("bbox") or []) if len(point) >= 2]
        if points:
            xs, ys = [float(point[0]) for point in points], [float(point[1]) for point in points]
            merged.append([[min(xs), min(ys)], [max(xs), max(ys)]])
    return merged


def redact_pages(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for page in pages:
        items = [{**item} for item in page.get("items", [])]
        for row in _rows(items):
            for index in _sensitive_indexes(row):
                row[index]["text"] = MASK
        copy = {**page, "items": items}
        copy["text"] = "\n".join(str(item.get("text", "")) for row in _rows(items) for item in row)
        result.append(copy)
    return result


def privacy_boxes(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for page in pages:
        boxes = []
        for row in _rows(page.get("items", [])):
            boxes.extend(_merge_row_boxes(row, _sensitive_indexes(row)))
        result.append({"page": int(page.get("page") or 1), "boxes": boxes})
    return result
