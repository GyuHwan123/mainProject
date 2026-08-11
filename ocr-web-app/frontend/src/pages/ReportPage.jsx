import Sidebar from '../components/Sidebar';
import TopBar from '../components/TopBar';

export default function ReportPage() {
  return (
    <div className="app-shell">
      <Sidebar />

      <main className="main-panel">
        <TopBar title="Reports" actions={<button className="mini-button">리포트 내보내기</button>} />

        <div className="report-layout">
          <section className="page-card">
            <div className="metric-grid">
              <div className="metric-card">
                <span>정확도</span>
                <div className="metric-number">96.4%</div>
              </div>
              <div className="metric-card">
                <span>재현율</span>
                <div className="metric-number">93.2%</div>
              </div>
              <div className="metric-card">
                <span>유사 문서</span>
                <div className="metric-number">12개</div>
              </div>
            </div>

            <div className="table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>문서</th>
                    <th>정확도</th>
                    <th>재현율</th>
                    <th>상태</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td>프로젝트 발표자료</td>
                    <td>96.4%</td>
                    <td>93.2%</td>
                    <td>완료</td>
                  </tr>
                  <tr>
                    <td>영수증 정리</td>
                    <td>94.1%</td>
                    <td>91.8%</td>
                    <td>완료</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>

          <aside className="page-card">
            <h3>Embedding 분석</h3>
            <p>유사 문서 검색 결과</p>
            <div className="history-list">
              <div className="history-item">
                <span>프로젝트 4인 발표</span>
                <span className="badge">92%</span>
              </div>
              <div className="history-item">
                <span>기획안 요약본</span>
                <span className="badge">88%</span>
              </div>
              <div className="history-item">
                <span>회의록_2026</span>
                <span className="badge">86%</span>
              </div>
            </div>
          </aside>
        </div>
      </main>
    </div>
  );
}
