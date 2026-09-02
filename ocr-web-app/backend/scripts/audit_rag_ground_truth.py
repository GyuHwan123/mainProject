from __future__ import annotations

import json
from collections import Counter
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
GROUND_TRUTH = Path(r"C:\Users\2class_06\Desktop\kaggle\embedding\ground_truth_50.json")
CORPUS_ROOT = PROJECT_ROOT / "models" / "bge-m3" / "data" / "company_documents"
CHUNKS_PATH = CORPUS_ROOT / "processed_v2" / "company_chunks_v2.json"
CATALOG_PATH = CORPUS_ROOT / "metadata" / "document_catalog.json"
OUTPUT_JSON = Path(__file__).with_name("rag_eval_dataset_audit.json")
OUTPUT_MD = Path(__file__).with_name("rag_eval_dataset_audit.md")


# Only non-OK decisions are listed here. Everything else answerable is OK;
# unanswerable cases become NEGATIVE_OK after the corpus-wide phrase review.
DECISIONS = {
    "Q008": ("AMBIGUOUS", ["GA-003", "GA-004"], "질문에 부가세 포함/별도 조건이 없지만 기대 답변은 부가세 별도 400만원으로 단정한다. 두 문서에 같은 승인 구간도 존재한다."),
    "Q009": ("AMBIGUOUS", ["GA-004"], "문서는 '1인 예상비용' 100만원 초과를 기준으로 하나 질문은 120만원이 1인 비용인지 출장 전체 비용인지 밝히지 않는다."),
    "Q016": ("AMBIGUOUS", ["HR-004"], "질문의 '휴가'가 연차인지 병가·경조휴가인지 특정되지 않았다. 기대 답변은 연차에만 적용되는 인수인계 계획을 전제한다."),
    "Q022": ("NO_EVIDENCE", ["IS-002", "IS-001"], "개인정보 지침은 개인 클라우드의 '장기간' 저장만 금지하고, 정보보안 규정은 대외비 이상 자료에 한정한다. '잠깐' 업로드를 일률적으로 금지한다는 직접 근거가 없다."),
    "Q023": ("MULTI_ANSWER", ["SH-001", "SH-003"], "두 문서가 각각 아차사고의 안전포털 등록 의무를 직접 뒷받침한다. 질문은 단일 사실형인데 복수 문서가 독립 정답이 될 수 있다."),
    "Q027": ("MULTI_ANSWER", ["GA-002", "GA-003", "GA-004"], "카드 한도는 GA-002가 답하고, 120만원 구매의 부서장 승인 근거는 GA-003과 GA-004 양쪽에 중복 존재한다."),
    "Q028": ("AMBIGUOUS", ["IS-002", "IS-001"], "질문은 문서의 보안등급을 밝히지 않지만 기대 답변은 대외비 문서 절차를 전제한다. 개인정보 전송 절차는 명확하나 부서장 승인 의무는 대외비일 때만 적용된다."),
    "Q029": ("WRONG_DOCUMENT", ["ER-001"], "질문은 반복적 공개 모욕에 적용할 규정을 묻고 ER-001이 직접 답한다. HR-001은 괴롭힘이 확인된 뒤 가능한 징계를 언급할 뿐 질문의 정답 문서는 아니다."),
    "Q031": ("WRONG_DOCUMENT", ["HR-003"], "연락 시한과 무단결근 판단은 HR-003이 완결적으로 답한다. HR-001의 징계 조항은 질문하지 않은 후속 효과라 정답 문서 매핑이 과다하다."),
    "Q032": ("WRONG_DOCUMENT", ["GA-002"], "1인당 5만원 초과 회식의 팀장 사전승인은 GA-002에 직접 규정되어 있다. GA-004의 상위승인은 이해관계·예산 외 지출·예외거래 등에만 적용되며 질문에는 그런 조건이 없다."),
    "Q033": ("WRONG_DOCUMENT", ["ER-003"], "가족이 거래처에 고용된 이해상충의 회피·신고는 ER-003이 직접 답한다. 질문자가 전결권자라는 조건이 없어 GA-004 상위승인 조항은 필수 정답이 아니다."),
    "Q034": ("WRONG_DOCUMENT", ["IS-001"], "보안사고 시 초기화와 로그 삭제 금지는 IS-001이 직접 답한다. SH-003은 산업재해·사고 대응 문맥의 현장·CCTV·로그 보존 규정이어서 일반 보안사고의 우선 정답 문서로 부적절하다."),
    "Q035": ("WRONG_DOCUMENT", ["ER-002"], "질문은 성희롱 신고자의 불이익 금지 조항 위치를 묻고 ER-002가 정확히 답한다. ER-001은 별개의 직장 내 괴롭힘 사건 보호 규정이다."),
    "Q038": ("MULTI_ANSWER", ["SH-003", "SH-001", "SH-002"], "SH-003 하나가 작업중지부터 현장보존·목격자 확보까지 전체 순서를 완결적으로 제공한다. SH-001과 SH-002도 초기 대응을 중복 뒷받침하므로 필수 복수문서형이 아니다."),
    "Q039": ("MULTI_ANSWER", ["SH-004", "SH-002"], "SH-004 하나가 신고·초기소화·대피·집결까지 완결적으로 답한다. SH-002는 화재 알림과 신고를 독립적으로 중복 뒷받침한다."),
    "Q040": ("WRONG_DOCUMENT", ["ER-001"], "피해자 보호조치는 ER-001이 직접 답한다. HR-001은 괴롭힘의 징계 가능성을 다루며 질문한 보호조치의 근거 문서가 아니다."),
    "Q041": ("NEEDS_REVIEW", ["ER-002"], "신고·조사·보호조치가 모두 ER-002 한 문서에 있다. expected_documents도 한 문서뿐이므로 question_type='multi_document'는 실제 성격과 맞지 않는다."),
    "Q042": ("WRONG_DOCUMENT", ["IS-001"], "외부 생성형 AI 입력 금지는 IS-001 제12조가 직접 완결적으로 답한다. IS-002 최소수집 조항은 AI 입력 금지 근거가 아니며 필수 복수문서형도 아니다."),
    "Q043": ("MULTI_ANSWER", ["GA-003", "GA-004"], "GA-003 하나에 비교견적 기준과 구매 승인표가 모두 있어 완결 답변이 가능하다. GA-004도 부서장 승인 기준을 중복 제공한다."),
    "Q044": ("WRONG_DOCUMENT", ["HR-002"], "사직서 제출 시점과 자산·계정·문서 인수인계는 HR-002가 완결적으로 답한다. IS-001의 비밀번호 공유 금지는 질문에 없는 세부사항을 기대 답변이 확장한 것이다."),
    "Q045": ("WRONG_DOCUMENT", ["ER-003"], "접대 금지와 이해상충 회피·신고는 ER-003이 모두 직접 답한다. 질문자가 전결권자라는 조건이 없어 GA-004 상위승인은 필수 근거가 아니다."),
}


# Minimal source excerpts. Each tuple is (doc_id, section, page, excerpt).
EVIDENCE = {
    "Q001": [("HR-001", "제6조 (근로시간)", 1, "기본 근무시간은 09:00부터 18:00까지로 하며, 휴게시간은 12:00부터 13:00까지 1시간으로 한다.")],
    "Q002": [("HR-002", "제8조 (평가주기)", 1, "정기 인사평가는 매년 12월 실시한다. 평가요소는 목표달성도 50%, 직무역량 30%, 협업·가치실천 20%로 구성한다.")],
    "Q003": [("HR-003", "제10조 (근태정정)", 1, "누락 또는 오류가 있는 근태기록은 발생일로부터 5영업일 이내 정정신청한다. 월 마감 이후에는 인사팀 확인이 추가로 필요하다.")],
    "Q004": [("HR-004", "제4조 (병가)", 1, "연간 10일까지 유급 병가를 사용할 수 있다. 연속 3일 이상 사용 시 진단서 등 증빙을 제출한다.")],
    "Q005": [("HR-005", "제1조 (급여지급일)", 1, "월 급여는 매월 25일 지급한다. 지급일이 휴일인 경우 직전 영업일에 지급한다.")],
    "Q006": [("GA-001", "제2장 국내출장비", 1, "숙박비 / 일반직원 / 1박 150,000원 한도")],
    "Q007": [("GA-002", "제3조 (사용한도)", 1, "일반 법인카드의 1회 사용한도는 1,000,000원이다. 한도 초과 예상 시 사전에 재무팀과 승인권자의 승인을 받아 임시한도 조정을 요청한다.")],
    "Q008": [("GA-003", "구매금액별 최종 승인권자", 1, "구매금액(부가세 별도) 1,000,000원 이상 ~ 5,000,000원 미만 / 부서장"), ("GA-004", "일반 구매", 1, "일반 구매 / 부서장 / 500만원 미만")],
    "Q009": [("GA-004", "제6조 (출장승인)", 1, "국내출장이라도 1인 예상비용이 1,000,000원을 초과하면 부서장 이상 승인을 받아야 한다.")],
    "Q010": [("IS-001", "제7조 (이동식 저장매체)", 1, "개인 USB 사용을 금지한다. 업무상 이동식 저장매체가 필요한 경우 정보보안팀이 승인한 암호화 USB만 사용할 수 있다.")],
    "Q011": [("IS-002", "제7조 (이메일 전송)", 1, "승인된 보안메일 또는 암호화 파일을 사용하고 비밀번호는 별도 채널로 전달한다.")],
    "Q012": [("SH-001", "제11조 (즉시조치)", 1, "인명사고 발생 시 작업을 중지하고 119 등 긴급구호가 필요하면 우선 조치한다.")],
    "Q013": [("SH-002", "2. 보호구", 1, "고소작업 / 안전모, 안전대")],
    "Q014": [("SH-003", "2. 보고 기준", 1, "병원진료가 필요한 사고는 구두보고 후 2시간 이내 1차 사고보고서를 작성한다.")],
    "Q015": [("SH-004", "2. 대피", 1, "지정 집결지인 본관 동측 주차장으로 이동한 후 부서별 인원점검에 응한다.")],
    "Q016": [("HR-004", "제1조 (연차 신청)", 1, "3일 이상 연속 연차는 업무인수인계 계획을 첨부한다.")],
    "Q017": [("HR-004", "제4조 (병가)", 1, "연속 3일 이상 사용 시 진단서 등 증빙을 제출한다.")],
    "Q018": [("GA-001", "제2장 국내출장비", 1, "숙박비 / 일반직원 / 1박 150,000원 한도 / 팀장 이상 / 1박 180,000원 한도")],
    "Q019": [("GA-002", "제11조 (증빙제출)", 1, "결제 후 3영업일 이내 전자영수증 또는 매출전표를 지출내역에 첨부한다.")],
    "Q020": [("GA-003", "제3조 (견적비교)", 1, "건당 3,000,000원 이상 구매는 원칙적으로 2개 이상의 비교견적을 확보한다.")],
    "Q021": [("IS-001", "제6조 (계정공유 금지)", 1, "비밀번호, OTP, 인증토큰을 타인과 공유해서는 안 된다.")],
    "Q022": [("IS-002", "제8조 (로컬 저장)", 1, "개인정보 파일을 개인 PC 바탕화면이나 개인 클라우드에 장기간 저장해서는 안 된다."), ("IS-001", "제3조 (정보등급)", 1, "대외비 이상 자료는 승인되지 않은 외부 저장소에 업로드할 수 없다.")],
    "Q023": [("SH-001", "제14조 (아차사고)", 1, "아차사고도 안전포털에 등록하고 재발방지 조치를 수립한다."), ("SH-003", "2. 보고 기준", 1, "아차사고를 포함하여 안전포털에 등록한다.")],
    "Q024": [("SH-004", "2. 대피", 1, "가장 가까운 비상계단을 이용하고 엘리베이터는 사용하지 않는다.")],
    "Q025": [("ER-003", "제5조 (선물)", 1, "1인 기준 50,000원을 초과하는 경우 원칙적으로 수령하지 않고 컴플라이언스팀에 문의한다.")],
    "Q026": [("GA-001", "제5조 (숙박비 초과)", 1, "한도를 초과하는 경우 사전에 본부장 승인을 받아야 한다. 사전승인이 없으면 한도액까지만 정산할 수 있다."), ("GA-002", "제5조 (출장 중 사용)", 1, "출장 숙박 및 교통비는 출장비규정의 항목별 한도 내에서 사용한다.")],
    "Q027": [("GA-002", "제3조 (사용한도)", 1, "일반 법인카드의 1회 사용한도는 1,000,000원이다."), ("GA-003", "구매금액별 최종 승인권자", 1, "1,000,000원 이상 ~ 5,000,000원 미만 / 부서장"), ("GA-004", "일반 구매", 1, "일반 구매 / 부서장 / 500만원 미만")],
    "Q028": [("IS-001", "제10조 (외부메일)", 1, "대외비 문서를 외부 이메일로 전송할 경우 부서장 승인과 암호화 조치를 적용한다."), ("IS-002", "제7조 (이메일 전송)", 1, "승인된 보안메일 또는 암호화 파일을 사용하고 비밀번호는 별도 채널로 전달한다.")],
    "Q029": [("ER-001", "제3조 (예시행위)", 1, "반복적 폭언·모욕, 공개적 망신주기 등은 구체적 상황에 따라 괴롭힘에 해당할 수 있다."), ("HR-001", "제18조 (징계사유)", 2, "직장 내 괴롭힘 등은 사실조사와 인사위원회 심의를 거쳐 징계할 수 있다.")],
    "Q030": [("SH-002", "3. 비상상황", 1, "화학물질 누출은 임의로 닦지 말고 물질안전보건자료와 누출대응 절차에 따른다."), ("SH-003", "2. 보고 기준", 1, "화학물질 누출을 포함하여 안전포털에 등록한다.")],
    "Q031": [("HR-003", "제5조 (결근)", 1, "질병 등 긴급사유가 있을 때는 당일 오전 10시까지 상급자에게 연락하고 증빙을 추후 제출한다."), ("HR-001", "제18조 (징계사유)", 2, "무단결근 ... 등은 사실조사와 인사위원회 심의를 거쳐 징계할 수 있다.")],
    "Q032": [("GA-002", "제6조 (회의 및 회식)", 1, "1인당 50,000원을 초과하는 회식은 팀장 사전승인을 받는다."), ("GA-004", "제4조 (상위승인)", 1, "전결권자가 이해관계자이거나 예산 외 지출, 예외거래 ... 인 경우 ... 상위 결재를 받아야 한다.")],
    "Q033": [("ER-003", "제9조 (사적이해관계 신고)", 1, "본인 또는 가족이 거래처에 ... 고용 ... 이해관계를 갖는 경우 관련 구매·평가·계약 의사결정에서 회피하고 ... 신고한다."), ("GA-004", "제4조 (상위승인)", 1, "전결권자가 이해관계자인 경우 ... 상위 결재를 받아야 한다.")],
    "Q034": [("IS-001", "제16조 (보안사고)", 1, "즉시 정보보안팀에 신고하고 임의로 로그를 삭제하거나 시스템을 초기화하지 않는다."), ("SH-003", "4. 금지행위", 1, "사고 은폐, 임의 현장정리, CCTV·로그 삭제 ... 는 금지한다.")],
    "Q035": [("ER-002", "제9조 (피해자 보호)", 1, "신고를 이유로 인사상 불이익을 주어서는 안 된다."), ("ER-001", "제8조 (보호조치)", 1, "신고자·피해자에 대한 불리한 처우를 금지한다.")],
    "Q036": [("GA-001", "제1조 (출장신청)·제10조 (정산기한)", 1, "출장 전 그룹웨어에 ... 예상비용을 등록하고 ... 승인을 받아야 한다. 출장 종료 후 5영업일 이내 출장보고서와 증빙을 제출한다."), ("GA-004", "제6조 (출장승인)", 1, "1인 예상비용이 1,000,000원을 초과하면 부서장 이상 승인을 받아야 한다."), ("GA-002", "제11조 (증빙제출)", 1, "결제 후 3영업일 이내 전자영수증 또는 매출전표를 지출내역에 첨부한다.")],
    "Q037": [("IS-002", "제12조 (오발송)", 1, "즉시 회수·삭제 요청을 하고 개인정보보호책임자실 및 정보보안팀에 보고한다."), ("IS-001", "제16조 (보안사고)", 1, "임의로 로그를 삭제하거나 시스템을 초기화하지 않는다.")],
    "Q038": [("SH-003", "1. 사고 발생 즉시", 1, "작업중지 및 2차 사고 방지 → 부상자 상태 확인·119·응급조치 → 즉시 보고 → 사고현장 보존 및 목격자 확보"), ("SH-001", "제11조 (즉시조치)", 1, "인명사고 발생 시 작업을 중지하고 119 등 긴급구호가 필요하면 우선 조치한다."), ("SH-002", "3. 비상상황", 1, "인명구조가 필요한 경우 ... 즉시 119 및 현장책임자에게 연락한다.")],
    "Q039": [("SH-004", "1. 화재 발견 시·2. 대피", 1, "비상벨을 작동한다. 119에 ... 알린다. ... 가장 가까운 비상계단을 이용하고 엘리베이터는 사용하지 않는다."), ("SH-002", "3. 비상상황", 1, "화재 발견 시 주변에 알리고 가장 가까운 비상벨 또는 신고체계를 이용한다.")],
    "Q040": [("ER-001", "제8조 (보호조치)", 1, "근무장소 변경, 유급휴가 등 피해자 보호조치를 검토하고 신고자·피해자에 대한 불리한 처우를 금지한다."), ("HR-001", "제18조 (징계사유)", 2, "직장 내 괴롭힘 등은 ... 징계할 수 있다.")],
    "Q041": [("ER-002", "제6조·제8조·제9조", 1, "인사팀 전담창구 또는 익명제보시스템을 이용할 수 있다. ... 조사는 피해자의 의사를 존중하고 ... 근무장소 변경, 유급휴가, 접촉 제한 등 ... 보호조치를 시행할 수 있다.")],
    "Q042": [("IS-001", "제12조 (생성형 AI 사용)", 1, "회사 기밀, 고객 개인정보, 소스코드 비밀키를 승인되지 않은 외부 생성형 AI 서비스에 입력해서는 안 된다."), ("IS-002", "제1조 (최소수집)", 1, "개인정보는 명확한 업무목적에 필요한 최소 범위로 수집한다.")],
    "Q043": [("GA-003", "제3조 (견적비교)·구매금액별 최종 승인권자", 1, "3,000,000원 이상 구매는 ... 2개 이상의 비교견적을 확보한다. 1,000,000원 이상 ~ 5,000,000원 미만 / 부서장"), ("GA-004", "일반 구매", 1, "일반 구매 / 부서장 / 500만원 미만")],
    "Q044": [("HR-002", "제18조 (퇴직절차)", 1, "퇴직희망일 30일 전 사직서를 제출하고, 자산·계정·문서 인수인계를 완료한다."), ("IS-001", "제6조 (계정공유 금지)", 1, "비밀번호, OTP, 인증토큰을 타인과 공유해서는 안 된다.")],
    "Q045": [("ER-003", "제6조·제9조", 1, "계약, 평가, 입찰 등 의사결정에 영향을 주는 접대는 금지한다. ... 관련 구매·평가·계약 의사결정에서 회피하고 ... 신고한다."), ("GA-004", "제4조 (상위승인)", 1, "전결권자가 이해관계자인 경우 ... 상위 결재를 받아야 한다.")],
}


def build() -> tuple[dict, str]:
    ground_truth = json.loads(GROUND_TRUTH.read_text(encoding="utf-8"))
    chunks = json.loads(CHUNKS_PATH.read_text(encoding="utf-8"))
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    titles = {item["doc_id"]: item["title"] for item in catalog["documents"]}
    corpus_text = "\n".join(chunk["text"] for chunk in chunks)

    # Explicitly exercise all 99 chunks for the five negative questions.
    negative_terms = {
        "Q046": ["구내식당", "점심 메뉴", "식단"],
        "Q047": ["직원 주차장", "주차장 이용", "주차시간"],
        "Q048": ["개인 노트북", "노트북 구매비", "구매비 지원"],
        "Q049": ["사내 카페", "직원 할인", "카페 할인"],
        "Q050": ["재택근무", "원격근무", "한 달에 최대"],
    }
    negative_hits = {
        qid: [term for term in terms if term in corpus_text]
        for qid, terms in negative_terms.items()
    }

    results = []
    for case in ground_truth["cases"]:
        qid = case["question_id"]
        if qid in DECISIONS:
            status, recommended, reason = DECISIONS[qid]
        elif not case["answerable"]:
            hits = negative_hits[qid]
            status = "NEGATIVE_ERROR" if hits else "NEGATIVE_OK"
            recommended = []
            reason = (
                f"전체 99개 청크에서 관련 표현이 발견됨: {', '.join(hits)}"
                if hits else
                "18개 문서의 전체 99개 v2 청크를 검사했으며 질문의 구체적 사실을 제공하는 근거가 없다."
            )
        else:
            status = "OK"
            recommended = list(case["expected_documents"])
            reason = "질문이 평가 가능하며 기대 문서·조항·답변이 실제 문서 근거와 일치한다. 질문 유형도 허용 가능한 범위다."

        evidence = [
            {
                "doc_id": doc_id,
                "title": titles[doc_id],
                "section": section,
                "page": page,
                "excerpt": excerpt,
            }
            for doc_id, section, page, excerpt in EVIDENCE.get(qid, [])
        ]
        results.append({
            "question_id": qid,
            "question": case["question"],
            "question_type": case["question_type"],
            "answerable": case["answerable"],
            "expected_documents": case["expected_documents"],
            "expected_document_titles": case["expected_document_titles"],
            "expected_sections": case["expected_sections"],
            "expected_answer": case["expected_answer"],
            "recommended_documents": recommended,
            "status": status,
            "evidence": evidence,
            "reason": reason,
        })

    counts = Counter(row["status"] for row in results)
    problematic = [row for row in results if row["status"] not in {"OK", "NEGATIVE_OK"}]
    payload = {
        "audit_name": "RAG evaluation dataset semantic consistency audit",
        "source_ground_truth": str(GROUND_TRUTH),
        "corpus": {
            "catalog": str(CATALOG_PATH.resolve()),
            "v2_chunks": str(CHUNKS_PATH.resolve()),
            "pdf_directory": str((CORPUS_ROOT / "documents").resolve()),
            "document_count": len(catalog["documents"]),
            "chunk_count": len(chunks),
            "database_used": False,
            "retrieval_scores_used": False,
        },
        "summary": {
            "total": len(results),
            "ok": counts["OK"],
            "suspected_revision_needed": len(problematic),
            "status_counts": dict(sorted(counts.items())),
            "problem_question_ids": [row["question_id"] for row in problematic],
        },
        "results": results,
    }

    lines = [
        "# RAG 평가 데이터셋 50문항 정합성 검수",
        "",
        "- 검수 기준: 원본 PDF 18개, 로컬 v2 청크 99개, 문서 카탈로그",
        "- 미사용: Supabase DB, Retrieval 결과, similarity, Reranker, Facet-Evidence Gate",
        f"- 전체: {len(results)}문항",
        f"- OK: {counts['OK']}문항",
        f"- 수정 필요 의심: {len(problematic)}문항",
        "",
        "## 상태별 개수",
        "",
        "| 상태 | 개수 |",
        "|---|---:|",
        *[f"| {status} | {count} |" for status, count in sorted(counts.items())],
        "",
        "## 문항별 결과",
        "",
    ]
    for row in results:
        lines.extend([
            f"### {row['question_id']} - {row['status']}",
            "",
            f"- question: {row['question']}",
            f"- question_type: `{row['question_type']}`",
            f"- answerable: `{str(row['answerable']).lower()}`",
            f"- expected_documents: {', '.join(row['expected_documents']) or '(없음)'}",
            f"- expected_document_titles: {', '.join(row['expected_document_titles']) or '(없음)'}",
            f"- expected_sections: {' / '.join(row['expected_sections']) or '(없음)'}",
            f"- expected_answer: {row['expected_answer']}",
            f"- recommended_documents: {', '.join(row['recommended_documents']) or '(없음)'}",
            f"- reason: {row['reason']}",
        ])
        if row["evidence"]:
            lines.append("- evidence:")
            for ev in row["evidence"]:
                lines.append(
                    f"  - `{ev['doc_id']}` {ev['title']} / {ev['section']} / p.{ev['page']}: “{ev['excerpt']}”"
                )
        else:
            lines.append("- evidence: 전체 코퍼스 검사 결과 직접 근거 없음")
        lines.append("")

    return payload, "\n".join(lines)


if __name__ == "__main__":
    if OUTPUT_JSON.exists() or OUTPUT_MD.exists():
        raise FileExistsError("Audit output already exists; refusing to overwrite it.")
    payload, markdown = build()
    OUTPUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    OUTPUT_MD.write_text(markdown, encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
