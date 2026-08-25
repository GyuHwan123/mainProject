# 네오웍스테크 주식회사 RAG 테스트 코퍼스

- 총 문서: 18개 PDF
- 목적: OCR, Chunking, Embedding, Vector Search, Reranking, LLM 답변생성 성능평가
- 주의: 모든 회사명·금액·규정은 가상 데이터이며 실제 법규 준수 판단에 사용하지 마세요.

## 의도된 중복 주제
- 취업규칙 / 근태관리 / 휴가휴직
- 출장비 / 법인카드 / 위임전결
- 정보보안 / 개인정보보호
- 안전보건 / 작업장안전 / 사고대응 / 화재비상대응
- 구매계약 / 위임전결

## 권장 RAG 평가
- Retrieval: Hit@K, Recall@K, MRR, nDCG
- Reranker 전후 비교: Top-10 후보 -> Top-3 재정렬
- Generation: 정답성, 출처 일치율, 근거충실도, hallucination 여부
