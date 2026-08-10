import Sidebar from '../components/Sidebar';
import TopBar from '../components/TopBar';

export default function OCRPage() {
  return (
    <div className="app-shell">
      <Sidebar />

      <main className="main-panel">
        <TopBar title="OCR Studio" actions={<button className="mini-button">업로드</button>} />

        <div className="ocr-layout">
          <aside className="sidebar-panel">
            <h3>문서 목록</h3>
            <div className="file-list">
              <div className="file-item">프로젝트_4인_발표.pdf</div>
              <div className="file-item">기획안_2026_서류.pdf</div>
              <div className="file-item">영수증_0527.pdf</div>
            </div>
          </aside>

          <section className="document-stage">
            <div className="stage-toolbar">
              <div className="tool-group">
                <span className="tool-button">⌕</span>
                <span className="tool-button">✎</span>
                <span className="tool-button">◫</span>
              </div>
              <div className="tool-group">
                <span className="tool-button">↺</span>
                <span className="tool-button">↻</span>
              </div>
            </div>

            <div className="stage-content">
              <strong>프로젝트 4인 발표</strong>
              <br /><br />
              OCR 텍스트 추출 영역입니다. 이곳에 이미지 또는 PDF 문서 내용이 인식되어 표시됩니다.
              <br /><br />
              - OCR 엔진: EasyOCR, Tesseract, RapidOCR
              <br />
              - 문서 요약: Gemma2:2b 기반 AI 요약
              <br />
              - 결과: 재현율, 정확도, 문맥 요약
            </div>
          </section>

          <aside className="chat-panel">
            <h3>AI 챗봇</h3>
            <div className="message user">이 문서 주요 내용을 한 줄로 요약해줘</div>
            <div className="message">프로젝트는 OCR/LLM/Vector Embedding을 활용해 문서 인식과 자동 요약을 수행합니다.</div>
            <div className="message user">특정 항목만 추출해줘</div>
            <input className="chat-input" placeholder="질문을 입력하세요" />
          </aside>
        </div>
      </main>
    </div>
  );
}
