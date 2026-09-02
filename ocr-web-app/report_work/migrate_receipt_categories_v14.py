from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path


CATEGORIES = {
    "외식/식사", "카페/음료", "식품/장보기", "생활용품", "의류/패션",
    "취미/선물", "미용/뷰티", "도서", "전자제품/문구", "대중교통",
    "주유/차량", "의료", "문화", "레저/스포츠",
}

TEST20 = {
    "test01.jpg": "의류/패션", "test02.jpg": "생활용품", "test03.jpg": "외식/식사",
    "test04.jpg": "외식/식사", "test05.jpg": "취미/선물", "test06.jpg": "외식/식사",
    "test07.jpg": "카페/음료", "test08.jpg": "카페/음료", "test09.jpg": "식품/장보기",
    "test10.jpg": "외식/식사", "test11.jpg": "미용/뷰티", "test12.jpg": "외식/식사",
    "test13.jpg": "의료", "test14.jpg": "의류/패션", "test15.jpg": "의류/패션",
    "test16.jpg": "의류/패션", "test17.jpg": "외식/식사", "test18.jpg": "외식/식사",
    "test19.jpg": "카페/음료", "test20.jpg": "생활용품",
}

VERIFIED54 = {
    "receipt_001.jpg": "취미/선물", "receipt_002.jpg": "미용/뷰티",
    "receipt_003.jpg": "도서", "receipt_004.jpg": "전자제품/문구",
    "receipt_005.jpg": "대중교통", "receipt_006.jpg": "대중교통",
    "receipt_007.jpg": "대중교통", "receipt_008.jpg": "대중교통",
    "receipt_009.jpg": "대중교통", "receipt_010.jpg": "대중교통",
    "receipt_011.jpg": "주유/차량", "receipt_012.jpg": "주유/차량",
    "receipt_013.jpg": "주유/차량", "receipt_014.jpg": "미용/뷰티",
    "receipt_015.jpg": "식품/장보기", "receipt_016.jpg": "식품/장보기",
    "receipt_017.jpg": "레저/스포츠", "receipt_018.jpg": "전자제품/문구",
    "receipt_019.jpg": "외식/식사", "receipt_020.jpg": "카페/음료",
    "receipt_021.jpg": "카페/음료", "receipt_022.jpg": "외식/식사",
    "receipt_023.jpg": "식품/장보기", "receipt_024.jpg": "외식/식사",
    "receipt_025.jpg": "외식/식사", "receipt_026.jpg": "미용/뷰티",
    "receipt_027.jpg": "외식/식사", "receipt_028.jpg": "외식/식사",
    "receipt_029.jpg": "식품/장보기", "receipt_030.jpg": "외식/식사",
    "receipt_031.jpg": "식품/장보기", "receipt_032.jpg": "외식/식사",
    "receipt_033.jpg": "외식/식사", "receipt_034.jpg": "의료",
    "receipt_035.jpg": "문화", "receipt_36.jpg": "외식/식사",
    "receipt_037.jpg": "도서", "receipt_038.jpg": "도서",
    "receipt_039.jpg": "취미/선물", "receipt_040.jpg": "식품/장보기",
    "receipt_041.jpg": "외식/식사", "receipt_042.jpg": "식품/장보기",
    "receipt_043.jpg": "미용/뷰티", "receipt_044.jpg": "외식/식사",
    "receipt_045.jpg": "카페/음료", "receipt_046.jpg": "식품/장보기",
    "receipt_047.jpg": "카페/음료", "receipt_048.jpg": "식품/장보기",
    "receipt_049.jpg": "식품/장보기", "receipt_050.jpg": "카페/음료",
    "receipt_051.jpg": "외식/식사", "receipt_052.jpg": "식품/장보기",
    "receipt_053.jpg": "식품/장보기", "receipt_054.jpg": "취미/선물",
}


def migrate(path: Path, mapping: dict[str, str]) -> None:
    rows = json.loads(path.read_text(encoding="utf-8"))
    names = {str(row.get("image")) for row in rows}
    missing = names - mapping.keys()
    extra = mapping.keys() - names
    if missing or extra:
        raise ValueError(f"{path}: mapping mismatch missing={sorted(missing)} extra={sorted(extra)}")
    backup = path.with_suffix(path.suffix + ".bak")
    if not backup.exists():
        shutil.copy2(path, backup)
    for row in rows:
        row["카테고리"] = mapping[str(row["image"])]
    invalid = {row["카테고리"] for row in rows} - CATEGORIES
    if invalid:
        raise ValueError(f"{path}: invalid categories {sorted(invalid)}")
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"updated {path} ({len(rows)} rows)")


def main() -> None:
    if len(sys.argv) != 4:
        raise SystemExit("usage: migrate_receipt_categories_v14.py TAXONOMY20 ORIGINAL20 VERIFIED54")
    migrate(Path(sys.argv[1]), TEST20)
    migrate(Path(sys.argv[2]), TEST20)
    migrate(Path(sys.argv[3]), VERIFIED54)


if __name__ == "__main__":
    main()
