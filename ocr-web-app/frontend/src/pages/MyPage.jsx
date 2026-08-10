import Sidebar from '../components/Sidebar';
import TopBar from '../components/TopBar';

export default function MyPage() {
  return (
    <div className="app-shell">
      <Sidebar />

      <main className="main-panel">
        <TopBar title="My Page" actions={<button className="mini-button">프로필 수정</button>} />

        <div className="mypage-grid">
          <section className="page-card">
            <h3>요금제 설정</h3>
            <div className="plan-card">
              <div>현재 플랜</div>
              <div className="plan-price">Pro</div>
              <p>월 29,000원 · 문서 2,000건 / 월</p>
            </div>
            <div className="history-list" style={{ marginTop: 20 }}>
              <div className="history-item">
                <span>개인 요금제</span>
                <span className="badge">활성</span>
              </div>
              <div className="history-item">
                <span>팀 요금제</span>
                <span>업그레이드 가능</span>
              </div>
            </div>
          </section>

          <aside className="page-card">
            <h3>히스토리</h3>
            <div className="history-list">
              <div className="history-item">
                <span>프로젝트 발표자료</span>
                <span>2026-08-10</span>
              </div>
              <div className="history-item">
                <span>영수증 정리</span>
                <span>2026-08-08</span>
              </div>
              <div className="history-item">
                <span>회의록 요약</span>
                <span>2026-08-02</span>
              </div>
            </div>
          </aside>
        </div>
      </main>
    </div>
  );
}

