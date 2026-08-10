import Sidebar from '../components/Sidebar';
import TopBar from '../components/TopBar';

export default function DashboardPage() {
  return (
    <div className="app-shell">
      <Sidebar />

      <main className="main-panel">
        <TopBar title="Dashboard" actions={<button className="mini-button">+ 새 문서</button>} />

        <div className="stat-row">
          <div className="stat-card">
            <h3>총 문서</h3>
            <strong>184</strong>
          </div>
          <div className="stat-card alt">
            <h3>OCR 정확도</h3>
            <strong>96.4%</strong>
          </div>
        </div>

        <div className="dashboard-grid">
          <section className="page-card">
            <div className="documents-list">
              <div className="document-item">
                <div>
                  <div className="document-title">계약서_2026_v2.pdf</div>
                  <small>업로드 2일 전</small>
                </div>
                <span className="badge">완료</span>
              </div>
              <div className="document-item">
                <div>
                  <div className="document-title">영수증_0527.pdf</div>
                  <small>업로드 4시간 전</small>
                </div>
                <span className="badge">검토 중</span>
              </div>
              <div className="document-item">
                <div>
                  <div className="document-title">신용대출_요약.docx</div>
                  <small>업로드 1주 전</small>
                </div>
                <span className="badge">완료</span>
              </div>
            </div>
          </section>

          <section className="page-card">
            <h3>최근 문서 미리보기</h3>
            <div className="document-preview">
              <div className="paper">
                <strong>프로젝트 4인 발표</strong>
                <br />
                <br />
                OCR 출력 텍스트 예시입니다. <br />
                - AI 핵심 기술: OCR, LLM, Vector Embedding <br />
                - 정확도: 96.4% <br />
                - 재현율: 93.2%
              </div>
            </div>
          </section>
        </div>
      </main>
    </div>
  );
}
