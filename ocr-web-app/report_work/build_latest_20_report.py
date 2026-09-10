"""Build a Markdown report from read-only snapshots of persisted evaluations."""
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from statistics import mean, median

ROOT = Path(__file__).resolve().parents[1]
data = json.loads((ROOT / 'report_work/latest_20_evaluation_source.json').read_text(encoding='utf-8'))
previous = json.loads((ROOT / 'report_work/previous_20_evaluation_source.json').read_text(encoding='utf-8'))
batch = data['batch']
items = {r['id']: r for r in data['items']}
rows = sorted(data['evaluations'], key=lambda r: items[r['item_id']]['dataset_index'])
assert len(rows) == 20 and batch['summary_metrics']['requested_count'] == 20
assert all(r['status'] == 'COMPLETED' for r in rows)
labels = {'merchant':'상호명','transaction_date':'구매일자','supply_amount':'공급가액','tax_amount':'부가세','discount_amount':'할인액','total_amount':'총 결제액','payment_method':'결제방식','expense_category':'카테고리','category':'카테고리','total_quantity':'총수량','document_type':'문서유형','items.count':'품목 개수','items.name':'품목명','items.quantity':'품목 수량','items.unit_price':'품목 단가','items.total_amount':'품목 금액','item_name_f1':'상품명 F1','item_quantity':'상품 수량','item_unit_price':'상품 단가','item_total_amount':'상품 금액'}
def name(r): return items[r['item_id']]['source_file_name']
def score(r): return r['selection_rubric']['extraction_score']
def pct(a,b=1): return f'{a/b*100:.2f}%' if b else '미측정'
def val(v):
    if v is None: return 'null (미기재)'
    return str(v).replace('|', '\\|').replace('\n', ' / ')
def kst(v): return datetime.fromisoformat(v).astimezone(timezone(timedelta(hours=9))).strftime('%Y-%m-%d %H:%M:%S KST')
def validation(r): return (r.get('pipeline_trace') or {}).get('validation',{}).get('extraction_validation') or {}
def fields(r):
    for key, detail in r['field_scores'].items():
        if key != 'items':
            yield key, labels.get(key,key), detail
        else:
            yield 'items.count','품목 개수',{'correct':detail['count_correct'],'expected':detail['expected_count'],'actual':detail['actual_count']}
            for item in detail.get('items',[]):
                actual = item.get('matched_actual_index')
                for field, d in item.get('fields',{}).items():
                    yield 'items.'+field, f"정답 품목 {item['index']+1} → 예측 {actual+1 if actual is not None else '없음'} / {labels.get('items.'+field,field)}",d

n=len(rows)
requested=20
full=sum(r['complete_match'] for r in rows)
partial=n-full
avg=mean(map(score,rows))
correct=sum(r['correct_fields'] for r in rows)
evaluated=sum(r['evaluated_fields'] for r in rows)
lat=sorted(r['latency_ms'] for r in rows if r['latency_ms']>0)
decisions=Counter(validation(r).get('decision','미측정') for r in rows)
fc=Counter(); ft=Counter(); impacted=defaultdict(set)
for r in rows:
    for key,_,d in fields(r):
        ft[key]+=1; fc[key]+=bool(d['correct'])
        if not d['correct']: impacted[key].add(name(r))
assert sum(ft.values())==evaluated and sum(fc.values())==correct
out=[]
def add(s=''):
    out.append(s)
    if s.startswith('#'):
        out.append('')
def table(headers, records):
    add('| '+' | '.join(headers)+' |'); add('| '+' | '.join('---' for _ in headers)+' |')
    for record in records: add('| '+' | '.join(val(x) for x in record)+' |')
    add()

add('# 영수증 OCR·구조화 모델 — 최근 20건 시도 평가 보고서')
add()
add(f'> **최종 추출 점수 {avg:.1f} / 100점 · 전체 필드 정확도 {pct(correct,evaluated)} · 완전 성공 {full}건 / 부분 성공 {partial}건 / 미완료 0건**')
add('> 가장 최근의 20건 요청 배치를 분석했다. test04.jpg를 포함한 20건 모두 등록·채점 완료된 최신 배치로 갱신했다.')
add()
add('이번 개정은 11:06 시작의 19건 완료 배치가 아닌, 11:34 시작의 새 20건 완료 배치를 기준으로 모든 통계를 다시 집계했다. 기존 보고서의 미등록 1건 표기와 80.3점은 이전 배치의 값이며 아래 결과로 대체한다.'); add()
add('## 1. 평가 데이터와 분석 범위')
table(['항목','내용'],[
('작성일','2026-09-10'),('배치 시작',kst(batch['created_at'])),('평가 기록 범위',kst(min(r['evaluated_at'] for r in rows))+' ~ '+kst(max(r['evaluated_at'] for r in rows))),('배치 ID',batch['id']),('모델',batch['model_name']),('정답 데이터',batch['dataset_name']),('평가 형태','정답 데이터와 영수증별 최종 구조화 결과를 비교하는 일괄 평가'),('요청 / 등록 / 채점','20 / 20 / 20건'),('DB 배치 상태',batch['status']),('점수 버전',', '.join(sorted(set(r['score_version'] for r in rows)))),('파이프라인 버전',', '.join(sorted(set(r['pipeline_version'] for r in rows)))),('모델·프롬프트 세부 버전','model_version / prompt_version 값 미기록. 모델 이름만으로 학습 가중치·프롬프트의 동일성을 보장할 수 없음')])
add('분석은 DB에 저장된 개별 `selection_rubric`, `field_scores`, 검증 판정과 오류 태그를 사용했다. 모델이나 OCR을 다시 실행하지 않았고 정답 라벨도 수정하지 않았다. 과거 배치와 섞어 20건을 채우지 않았다. 예시 이미지의 숫자는 사용하지 않았다.')
add()
add('## 2. 평가지표 및 최종 결과')
table(['지표','결과','분모·해석'],[
('현재 화면 기준 최종 추출 점수',f'{avg:.4f} / 100 → {avg:.1f}점','채점된 20건의 100점 추출 점수 산술평균'),('필드 정확도 — 전체 비교 수 기준',pct(correct,evaluated),f'{correct}/{evaluated}개 일치. 품목이 많은 영수증의 비중이 커짐'),('필드 정확도 — 영수증별 평균',pct(mean(r['field_accuracy'] for r in rows)),'각 영수증 필드 정확도를 같은 비중으로 평균. DB average_field_accuracy'),('완전 일치율 — 채점 결과 기준',pct(full,n),f'{full}/{n}건'),('완전 성공률 — 요청 기준',pct(full,requested),f'{full}/{requested}건'),('처리 완료율',pct(n,requested),'20/20건. 내용의 정답 여부와 별도'),('총 결제액 정확도',pct(fc['total_amount'],ft['total_amount']),f"{fc['total_amount']}/{ft['total_amount']}건"),('단일 JSON 지표',pct(mean(r['selection_rubric']['schema_rate'] for r in rows)),'필수 키 8개와 items 배열 조건 등 9개 조건 충족률의 평균'),('추출 검증 PASS',pct(decisions['PASS'],sum(decisions[k] for k in ['PASS','REVIEW','USER_CONFIRM'])),f"PASS {decisions['PASS']} / REVIEW {decisions['REVIEW']} / USER_CONFIRM {decisions['USER_CONFIRM']}"),('워크북 검증 성공',pct(sum(bool(r['workbook_result'].get('success')) for r in rows),n),'생성·검증 결과. 내용 전체의 정확성을 뜻하지 않음'),('평균 응답시간',f'{mean(lat)/1000:.3f}초',f'양수 측정값 {len(lat)}건'),('중앙값 응답시간',f'{median(lat)/1000:.3f}초','참고 통계'),('P95 응답시간',f'{lat[math.ceil(.95*len(lat))-1]/1000:.3f}초','오름차순 ceil(20×0.95)=19번째'),('최소 / 최대 응답시간',f'{min(lat)/1000:.3f} / {max(lat)/1000:.3f}초','측정된 분류 요청 기준'),('분류 요청 시간 합계',f'{sum(lat)/1000:.3f}초','전체 작업의 실제 경과시간과 다름')])
add('응답시간은 프런트엔드에서 `/finance/records/classify` 요청 직전부터 응답까지 측정한 `latency_ms`다. OCR 업로드와 별도 평가 요청은 이 구간 밖에 있다. 따라서 “OCR 포함 전체 처리시간” 또는 “순수 모델 추론시간”으로 해석하면 안 된다.')
add()
add('### 2.1 현재 화면 점수와 DB 요약 점수가 다른 이유')
add(f"현재 화면은 추출 점수만 사용하므로 **{avg:.1f}점**이다. DB의 `summary_metrics.final_score_100`은 **{batch['summary_metrics']['final_score_100']:.4f}점**으로, 추출 평균 {avg:.4f}점에 속도 3점과 로컬 비용 2점을 더한 값이다. DB 요약 계산 코드에 이 가산식이 남아 있다. 이 보고서의 대표 점수는 현재 화면 기준으로 통일했다.")
add()
add('`summary_metrics.extraction_score_95`는 이름에 95가 있지만 현재 저장 요약에서는 100점 rubric의 평균을 담는다. 개별 행의 동명 열은 별도 호환 값일 수 있으므로 보고서는 `selection_rubric.extraction_score`를 직접 집계했다. 가산점이 있는 DB 수치를 추출 정확도로 인용하면 성능을 과대 표현하게 된다.')
add()
add('## 3. 20건 결과 요약과 성공·실패 정의')
table(['분류','건수','요청 20건 기준','판정 기준'],[('완전 성공',full,pct(full,20),'처리 COMPLETED이며 저장된 complete_match=true'),('부분 성공',partial,pct(partial,20),'채점 완료했으나 하나 이상의 비교가 불일치'),('처리 실패 — 확인된 오류',0,'0.00%','등록된 항목 중 FAILED 없음'),('미완료 — DB 미등록',0,'0.00%','요청 20건 모두 등록·완료')])
add(f'요청하신 3분류로 줄이면 **완전 성공 {full}건 / 부분 성공 {partial}건 / 실패·미완료 0건**이다. test04.jpg도 처리 완료했으며 정답 불일치가 있어 부분 성공으로 분류했다. 정답 불일치에 붙은 `error_analysis.status=FAILED`도 처리 단계의 실패와 구분한다.')
add()
add('```text')
add(f"완전 성공    {'█'*full}{'░'*(20-full)}  {full:2d}/20")
add(f"부분 성공    {'█'*partial}{'░'*(20-partial)}  {partial:2d}/20")
add('미등록      ░░░░░░░░░░░░░░░░░░░░   0/20')
add('```'); add()
add('## 4. 점수 산정 기준과 배점별 결과')
add('영수증 점수 = Σ(항목별 일치율 × 배점). 전체 점수 = 채점된 영수증 점수 합계 ÷ 20. 일반 필드는 정규화 후 일치/불일치로 1 또는 0을 부여한다. 상품 단가·수량·금액은 정답 품목 수를 분모로 한 일치 비율이고, 상품명은 F1이다.'); add()
components=rows[0]['selection_rubric']['components']
table(['항목','배점','평균 일치율 / F1','평균 획득점','평균 손실점'],[(labels.get(k,k),v['weight'],pct(mean(r['selection_rubric']['components'][k]['score'] for r in rows)),f"{mean(r['selection_rubric']['components'][k]['points'] for r in rows):.4f}",f"{v['weight']-mean(r['selection_rubric']['components'][k]['points'] for r in rows):.4f}") for k,v in components.items()])
add(f'총 배점 **{sum(v["weight"] for v in components.values())}점**, 평균 획득 **{avg:.4f}점**, 평균 손실 **{100-avg:.4f}점**이다. 소수점 반올림으로 표시값 합계에는 미세한 차이가 있을 수 있다.'); add()
add('### 4.1 비교 규칙과 해석상의 주의점')
add('- 문서유형·총수량은 100점 모델 점수에서 제외한다. 총수량은 저장된 일반 필드 정확도와 완전 일치 여부에는 포함될 수 있다. 카드번호는 두 지표 모두 제외한다.\n- 숫자는 쉼표·기호를 정리하고 소수 둘째 자리로 비교한다. 숫자 필드의 null은 비교 과정에서 0으로 정규화될 수 있다. 단, 할인액의 rubric은 정답이 비어 있으면 예측도 비어 있어야 만점이다.\n- 날짜·결제방식·카테고리 등은 정규화/동치 규칙을 적용한다. 상호명은 일반적으로 정규화 후 일치를 요구하며 OCR 박스가 분리된 조건에서 제한적으로 유사 매칭을 허용한다.\n- 상품은 이름 일치 가중치 3, 수량·단가·금액 각각 1의 후보 점수로 1:1 대응한다. 입력 순서 그대로 비교하는 방식이 아니다.\n- 상품명은 등록 별칭, 부분 포함, 문자열 유사도 0.72 이상 또는 토큰 겹침 등의 규칙을 사용한다. 따라서 문자열이 달라도 정답으로 판정될 수 있다.\n- 상품명 F1은 정밀도와 재현율의 조화평균으로 누락과 추가 품목을 함께 반영한다. 양쪽 상품 목록이 모두 비면 상품 점수는 만점이며, 정답 목록이 비고 예측만 있으면 0점이다.\n- 일반 필드 정확도는 비교 항목을 동일하게 세고, 100점 점수는 항목별 배점과 품목 평균을 사용한다. 두 숫자가 다른 것은 집계 방식의 차이다.\n- 처리 완료·JSON 스키마 충족·검증 PASS·완전 일치는 서로 다른 지표다. 하나의 성공률로 대체할 수 없다.')
add()
add('## 5. 필드별 정확도와 오류 규모')
table(['필드','일치 / 비교 수','정확도','불일치 비교 수','오류 영수증 수'],[(labels.get(k,k),f'{fc[k]}/{ft[k]}',pct(fc[k],ft[k]),ft[k]-fc[k],len(impacted[k])) for k in ft])
add('품목 오류의 “비교 수”는 품목별 필드 수이고 “오류 영수증 수”는 파일별 중복을 제거한 수다. 여러 필드가 틀린 한 영수증은 여러 행에 등장하므로 오류 영수증 수를 합해 전체 실패 건수로 사용하지 않는다.'); add()
add('## 6. 주요 오류 분석')
groups=[('공급가액·부가세',{'supply_amount','tax_amount'}),('품목 관련',{'items.count','items.name','items.quantity','items.unit_price','items.total_amount'}),('상호명',{'merchant'}),('총 결제액',{'total_amount'}),('카테고리',{'expense_category'})]
table(['오류 묶음','영향 영수증 수 / 20','파일'],[(label,len(set().union(*(impacted[k] for k in keys))),', '.join(sorted(set().union(*(impacted[k] for k in keys))))) for label,keys in groups])
for label,keys in groups:
    add(f'### {label}')
    examples=[]
    for r in rows:
        for k,title,d in fields(r):
            if k in keys and not d['correct']:
                examples.append((name(r),title,d['expected'],d['actual']))
    table(['파일','비교 항목','정답','예측'],examples[:8])
add('공급가액·부가세에서 정답 null과 예측 숫자의 차이는 단순 숫자 오인식뿐 아니라 “원문에 없는 세액을 채울 것인가”라는 라벨·후처리 정책 차이일 수 있다. 특히 receipt_006의 null→4,770/530은 원문 이미지·정답 작성 정책을 함께 검토해야 한다. 품목 오류는 대응된 품목 자체가 달라 여러 필드의 불일치가 연쇄 발생할 수 있으므로 각각을 독립적인 인식 오류로 단정하지 않는다.'); add()
tags=Counter(); tagdocs=defaultdict(set); codes=Counter(); reasons=Counter(); ocr=Counter()
for r in rows:
    for tag in r.get('error_tags') or []:
        tags[tag.get('category','UNKNOWN')]+=1; tagdocs[tag.get('category','UNKNOWN')].add(name(r)); codes[tag.get('code','UNKNOWN')]+=1
    reasons.update(set(validation(r).get('reasons') or []))
    ocr.update((r.get('ocr_impact') or {}).get('counts') or {})
add('### 6.1 저장된 자동 오류 태그')
table(['분류','태그 수','영향 영수증 수'],[(k,v,len(tagdocs[k])) for k,v in tags.most_common()])
table(['오류 코드','태그 수'],codes.most_common())
add('오류 태그는 규칙 기반 원인 추정이며 실제 원인 확정이 아니다. 동일 필드에 여러 태그가 붙거나 필드 불일치와 태그 수가 다를 수 있다.'); add()
add('### 6.2 OCR 영향 추정')
table(['코드','비교 수'],ocr.most_common())
add('SUCCESS는 OCR 근거와 정답 일치, LLM_RECOVERY는 근거 미검출이지만 일치, LIKELY_LLM_ERROR는 근거가 있으나 불일치, LIKELY_OCR_ERROR는 근거 미검출과 불일치다. 이 검사는 OCR 문자열에 정답이 보이는지에 기반하므로 CER/WER 또는 실제 OCR 정확도가 아니다. null 라벨·추론형 카테고리도 근거 미검출로 분류될 수 있어 OCR 오류로 일괄 귀속하면 안 된다.'); add()
add('## 7. 추출 검증과 사용자 확인')
table(['판정','건수','파일'],[(k,v,', '.join(name(r) for r in rows if validation(r).get('decision','미측정')==k)) for k,v in decisions.items()])
table(['검증 사유 코드','해당 영수증 수'],reasons.most_common())
add('사유 코드는 한 영수증 내 같은 코드를 중복 제거했다. 서로 다른 코드를 의미상 합치지 않았으므로 대시보드에서 유사 코드를 병합한 집계와 다를 수 있다. 검증은 정답 라벨과의 매칭 결과와 별도로 작동한다. 검증 PASS도 최종 사용자 확인을 대체하지 않는다.'); add()
add('## 8. 영수증별 전체 결과')
table(['파일','데이터 인덱스(0부터)','분류','추출점수','필드 일치','필드 정확도','총액 일치','검증','응답(초)'],[(name(r),items[r['item_id']]['dataset_index'],'완전 성공' if r['complete_match'] else '부분 성공',f'{score(r):.2f}',f"{r['correct_fields']}/{r['evaluated_fields']}",pct(r['field_accuracy']),'O' if r['selection_rubric']['total_amount_correct'] else 'X',validation(r).get('decision','미측정'),f"{r['latency_ms']/1000:.3f}") for r in rows])
add('데이터 인덱스는 전체 라벨 데이터 내 위치다. 0~19가 연속된다는 보장이 없으며 인덱스의 빈칸만으로 누락 파일을 확정할 수 없다.'); add()
add('## 9. 개별 영수증 상세 — 모든 불일치 값과 감점')
for r in rows:
    add(f"### {name(r)} — {score(r):.2f}점")
    add(f"- 평가 시각: {kst(r['evaluated_at'])}\n- 결과: {'완전 성공' if r['complete_match'] else '부분 성공'} / 필드 {r['correct_fields']}/{r['evaluated_fields']} 일치 / 응답 {r['latency_ms']/1000:.3f}초\n- 검증: {validation(r).get('decision','미측정')} / 사유: {', '.join(validation(r).get('reasons') or []) or '없음'}")
    add()
    losses=[(labels.get(k,k),v['weight'],f"{v['points']:.4f}",f"{v['weight']-v['points']:.4f}") for k,v in r['selection_rubric']['components'].items() if v['points']<v['weight']-1e-8]
    if losses: table(['감점 항목','배점','획득점','손실점'],losses)
    else: add('100점 평가 항목에서 감점 없음.\n')
    mismatches=[(title,d['expected'],d['actual']) for _,title,d in fields(r) if not d['correct']]
    if mismatches: table(['불일치 항목','정답','예측'],mismatches)
    else: add('저장된 비교 항목 전체 일치.\n')

add('## 10. 직전 20건 완료 배치와 참고 비교')
previtems={i['id']:i for i in previous['items']}
prev={previtems[r['item_id']]['source_file_name']:r for r in previous['evaluations']}
current={name(r):r for r in rows}
shared=sorted(current.keys() & prev.keys())
add(f"직전 배치는 {kst(previous['batch']['created_at'])}, ID `{previous['batch']['id']}`이며 20/20건이 완료됐다. 최신 시도와 혼합하지 않고 별도 비교했다."); add()
table(['항목','직전 완료 배치','최신 시도'],[('채점 건수',len(prev),n),('추출 평균',f'{mean(score(r) for r in prev.values()):.4f}',f'{avg:.4f}'),('완전 일치',sum(r['complete_match'] for r in prev.values()),full),('공통 파일 수',len(shared),len(shared))])
table(['공통 파일','직전 점수','최신 점수','차이','정답 데이터 동일'],[(key,f'{score(prev[key]):.2f}',f'{score(current[key]):.2f}',f'{score(current[key])-score(prev[key]):+.2f}','예' if prev[key]['normalized_ground_truth']==current[key]['normalized_ground_truth'] else '아니오') for key in shared])
add('직전에만 있는 파일: '+(', '.join(sorted(prev.keys()-current.keys())) or '없음')+'.')
add('최신에만 있는 파일: '+(', '.join(sorted(current.keys()-prev.keys())) or '없음')+'.')
if all(abs(score(prev[key])-score(current[key])) < 1e-8 for key in shared):
    add(f'공통 {len(shared)}개 파일의 점수는 모두 동일하다. 두 배치의 평균 차이는 이 비교에서 파일 구성 차이로 설명되며, 공통 파일의 점수 개선은 관찰되지 않았다.')
add('최신 배치는 test04.jpg를 포함해 20건 모두 완료됐다. 위 차집합은 배치 간 파일 구성 차이를 보여준다. 서로 다른 실행의 결과 차이만으로 모델 개선이나 퇴보를 입증할 수 없다.'); add()
add('## 11. 개선 우선순위와 다음 검증')
loss_rank=sorted(((k,v['weight']-mean(r['selection_rubric']['components'][k]['points'] for r in rows)) for k,v in components.items()),key=lambda x:x[1],reverse=True)
add('현재 평균 손실이 큰 항목은 '+', '.join(f'{labels.get(k,k)} {v:.2f}점' for k,v in loss_rank[:4])+'이다. 아래 조치는 저장 결과에 근거한 개선 제안이며 이번 보고서 작성에서 코드를 수정하거나 재평가하지 않았다.'); add()
table(['우선순위','조치','확인 방법'],[('1','요청 파일 목록과 업로드 이전 실패도 배치에 기록','20건 각각의 파일명·단계·오류가 남고 요청/등록/완료 수가 맞는지 확인'),('2','공급가액·세액의 미기재 처리와 정답 라벨 정책 정렬','null→숫자 사례의 원문, 모델 원출력, 후처리 결과를 나란히 비교'),('3','품목 행 대응과 단가·수량·합계 열 혼동 검토','다중 품목 오류 문서의 OCR 박스/표와 실제 행 대응을 확인'),('4','상호명 보존 및 가맹점·브랜드·발행사 구분','상호명 오류 12건의 원문 라벨과 예측 근거를 확인'),('5','DB 요약과 프런트엔드 점수 산식 통일','같은 배치의 저장 요약과 UI가 동일 점수를 반환하는지 확인'),('6','동일 파일·라벨·모델·프롬프트로 재평가','완료율, 100점 평균, 필드 정확도, 완전 일치율을 동일 분모로 비교')])
add('## 12. 재현 근거와 한계')
add('- 원본: `finance_evaluation_batches`, `finance_evaluation_items`, `finance_record_evaluations`의 읽기 전용 조회 결과.\n- 로컬 분석 스냅샷: `../report_work/latest_20_evaluation_source.json`, 비교용 `../report_work/previous_20_evaluation_source.json`.\n- 보고서 생성: `../report_work/build_latest_20_report.py`. 실행하면 동일 스냅샷으로 같은 표를 재생성한다.\n- 점수 정의: `../backend/app/services/finance_evaluation_scoring.py`의 `_selection_rubric`, `score_fields`, `_values_match`, `_match_items`.\n- 현재 UI 집계: `../frontend/src/pages/FinanceEvaluationPage.jsx`의 `summarize`, `scoredSummaries`, `evaluateFile`.\n- 저장 요약 집계: `../backend/app/services/supabase_document_finance_repository.py`의 `_refresh_finance_evaluation_summary`.\n- 오류 원인은 저장된 자동 분석을 요약했으며 원본 이미지 20건을 모두 육안 재검수한 결과가 아니다. 일부 불일치는 정답 작성 정책의 문제일 수 있다.\n- 이번 요청의 표본은 최신 20건 시도 한 배치다. 일반적인 영수증 전체에 대한 성능 추정이나 통계적 우월성 주장은 하지 않는다.')
path=ROOT/'reports/receipt_latest_20_evaluation_2026-09-10.md'
path.write_text('\n'.join(out)+'\n',encoding='utf-8')
print(json.dumps({'path':str(path),'score':avg,'complete':full,'partial':partial,'unregistered':0,'field_correct':correct,'field_total':evaluated,'lines':len(out)},ensure_ascii=False))
