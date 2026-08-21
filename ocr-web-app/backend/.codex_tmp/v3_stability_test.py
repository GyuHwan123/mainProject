import hashlib
import json
import time
from pathlib import Path

import httpx

ocr = '''Honne plus
www.homeplus.co.kr
한달 (신선일)이내 곧환/확불/결제변
2019/02/2016:15:48[수]
상품명
01*신도림재사용봄투20L
02 콤탕큰사발
포카칩오리지날124G
03
Singles_선탐바 설
04
05 포카집양파맛124G
닭
06 Singles_조선탕반
07연콤기했반300G
08 빠다코코낫300G
09 데카 !수탄드기획팩
스화이트288G
10
1까르보불닭큰컵
12후레쉬오렌지50%
13롯데초코칩쿠키69G
14
15
행사할인
X
총합 계
할인(에누리)
쿠매금액
4BC체크카드(우리)
칸드결제
할부개월 :일시불
적립/누척/가용
승인번호\":695259751
한화다이렉트 자동차보험
1.자동차보험 이벤트
계산원:(C)이*순
홈플러스소토어즈 신도림점
314-81-11803임일순
Tel)02-2618-2080
서울시 구로구 경인로 661
구매,점포에서
임대매장 예외
가능
TM:000104N0:0061
단가 수량 – 금액
490
490
950
950
390
2, 390
4, 490 24 490
390 2 390
2,
42 490 A 490
090 2,090
2,0001 2,000
4,790 L
42 990 790 11 2,990
1,000 2 2,000
1,000 1 1,000
590 1 590
9,900 11 9,900
9,900 9,900
_9,900
36,428
세
3,642
품
490
50,460
-9,900
40,560
5596-20**-****-096650
40,560
승인번호:44697631
20/519/519점
문의:1599-0512
대표번호:02)2618-2080
단, '정상(미개롱)상품, '영수봉/결제카드 지참
*표시 상품은 부가세 면세품목입니다.
과 세 물 품
가-
세 '물금
0K캐쉬백(5596-20**-****-0966)A
할인받고 적계타면 최대 45% 환급
자동차보험 _문의하신 모든분께~
공물러스 5천원 상품권 능청
자동차보험 문의:1899-6633
2.치아보험 상담 5천원,가입 2만원
019420190220000'''

prompt = f'''OCR 영수증을 학습 데이터와 동일한 한국어 JSON 형식으로 변환하세요. OCR에 없는 값은 null로 작성하세요. 반드시 JSON 객체 하나만 반환하세요. 구매물품은 실제 영수증의 각 상품 행을 모두 포함하세요.

출력 형식:
{{
  "가게명": null,
  "구매일자": null,
  "구매물품": [
    {{
      "상품명": null,
      "단가": null,
      "수량": null,
      "금액": null
    }}
  ],
  "총 물품 수량": null,
  "총 결제액": null,
  "카테고리": null,
  "결제방식": null,
  "카드번호": null
}}

[OCR 텍스트]
{ocr}'''

options = {"temperature": 0, "num_predict": 1200, "num_ctx": 8192, "repeat_penalty": 1.08}
model = "llama3b-receipt-v3:latest"
print("OCR_LENGTH:", len(ocr), flush=True)
print("PROMPT_LENGTH:", len(prompt), flush=True)
print("PROMPT_SHA256:", hashlib.sha256(prompt.encode("utf-8")).hexdigest(), flush=True)
print("GENERATION_OPTIONS:", json.dumps({"format": "json", "options": options}, ensure_ascii=False, sort_keys=True), flush=True)

with httpx.Client(timeout=900.0) as client:
    for index in range(1, 6):
        payload = {"model": model, "prompt": prompt, "stream": False, "keep_alive": "30m", "format": "json", "options": options}
        started = time.perf_counter()
        response = client.post("http://127.0.0.1:11434/api/generate", json=payload)
        response.raise_for_status()
        body = response.json()
        raw = body.get("response", "")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        array_keys = [key for key, value in (parsed or {}).items() if isinstance(value, list)] if isinstance(parsed, dict) else []
        korean_keys = [key for key in (parsed or {}) if any("가" <= char <= "힣" for char in key)] if isinstance(parsed, dict) else []
        print(f"=== RUN {index} RAW BEGIN ===", flush=True)
        print(raw, flush=True)
        print(f"=== RUN {index} RAW END ===", flush=True)
        print("SUMMARY:", json.dumps({"array_keys": array_keys, "array_counts": {key: len(parsed[key]) for key in array_keys} if isinstance(parsed, dict) else {}, "korean_keys": korean_keys, "seconds": round(time.perf_counter() - started, 1), "eval_count": body.get("eval_count")}, ensure_ascii=False), flush=True)
