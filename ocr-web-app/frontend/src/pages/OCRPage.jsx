import { useEffect, useRef, useState } from 'react';
import * as pdfjsLib from 'pdfjs-dist';
import pdfWorker from 'pdfjs-dist/build/pdf.worker.min.mjs?url';
import '../style/OCRPage.scss';
import { IoMdSettings } from "react-icons/io";
import { IoDocumentTextOutline } from "react-icons/io5";

pdfjsLib.GlobalWorkerOptions.workerSrc = pdfWorker;

const EMPTY_FILE_NAME = '문서를 선택해 주세요';

function PdfCanvas({ pdf, pageNumber, scale = 1.25, thumbnail = false }) {
  const canvasRef = useRef(null);

  useEffect(() => {
    if (!pdf) return undefined;
    let cancelled = false;
    let renderTask;
    pdf.getPage(pageNumber).then((page) => {
      if (cancelled || !canvasRef.current) return;
      const viewport = page.getViewport({ scale });
      const canvas = canvasRef.current;
      canvas.width = viewport.width;
      canvas.height = viewport.height;
      renderTask = page.render({ canvasContext: canvas.getContext('2d'), viewport });
    });
    return () => {
      cancelled = true;
      renderTask?.cancel();
    };
  }, [pdf, pageNumber, scale]);

  return <canvas ref={canvasRef} className={thumbnail ? 'pdf-thumb-canvas' : 'pdf-main-canvas'} />;
}


export default function OCRPage({ user }) {
  const [pdf, setPdf] = useState(null);
  const [fileName, setFileName] = useState(EMPTY_FILE_NAME);
  const [pageNumber, setPageNumber] = useState(1);
  const [pageTexts, setPageTexts] = useState([]);
  const [zoom, setZoom] = useState(1.05);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const inputRef = useRef(null);

  const loadPdf = async (file) => {
    if (!file || file.type !== 'application/pdf') {
      setError('PDF 파일만 업로드할 수 있습니다.');
      return;
    }
    setLoading(true);
    setError('');
    try {
      const data = await file.arrayBuffer();
      const document = await pdfjsLib.getDocument({ data }).promise;
      const texts = await Promise.all(
        Array.from({ length: document.numPages }, async (_, index) => {
          const page = await document.getPage(index + 1);
          const content = await page.getTextContent();
          return content.items.map((item) => item.str).join(' ').replace(/\s+/g, ' ').trim();
        }),
      );
      setPdf(document);
      setPageTexts(texts);
      setPageNumber(1);
      setFileName(file.name);
    } catch {
      setError('PDF를 읽지 못했습니다. 손상되었거나 지원하지 않는 파일일 수 있습니다.');
    } finally {
      setLoading(false);
    }
  };

  const downloadText = () => {
    const body = pageTexts.map((text, index) => `--- ${index + 1} 페이지 ---\n${text}`).join('\n\n');
    const url = URL.createObjectURL(new Blob([body], { type: 'text/plain;charset=utf-8' }));
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `${fileName.replace(/\.pdf$/i, '') || 'extracted'}-text.txt`;
    anchor.click();
    URL.revokeObjectURL(url);
  };
  const currentText = pageTexts[pageNumber - 1] || '';
  
  return (
    <div className="ocr-app-shell">
      <aside className="sidebar-panel">
        <div className="sidebar-brand">
          <img src="/DocAI.png" alt="DOCUNEX AI" />
        </div>
        <button className="new-project-button" onClick={() => inputRef.current?.click()}>＋ 새 프로젝트</button>
        <label className="sidebar-search">
          <span aria-hidden="true">⌕</span>
          <input type="search" placeholder="문서 검색..." />
        </label>
        <div className="sidebar-history">
          <h2>최근 문서</h2>
          <button className="history-item active" type="button">
            <span className="doc-icon" aria-hidden="true"><IoDocumentTextOutline /></span>
            <span className="doc-info">
              <strong>{fileName === EMPTY_FILE_NAME ? '최근 작업 문서.pdf' : fileName}</strong>
              <small>방금 전</small>
            </span>
          </button>
        </div>
        <div className="sidebar-user">
            {user?.profileImg ? (
                <img 
                src={user.profileImg} 
                alt={`${user.name}의 프로필`} 
                className="user-avatar" 
                />
            ) : (
                /* DB에 이미지가 없거나 null일 경우 이름의 첫 글자 표시 */
                <span className="user-avatar">
                {user?.name ? user.name.charAt(0) : 'U'}
                </span>
            )}

            <span className="user-details">
                <strong>{user?.name || '사용자'}</strong>
                <small>{user?.email || 'email@company.com'}</small>
            </span>

            <button type="button" aria-label="설정"><IoMdSettings /></button>
        </div>
      </aside>

      <main className="ocr-workspace">
        <header className="ocr-header">
          <div className="header-title">
            <button className="back-button" type="button" onClick={() => window.history.back()} aria-label="뒤로 가기">‹</button>
            <div><h1>OCR Viewer</h1></div>
          </div>
          <div className="ocr-header-actions">
            <span className="extract-method"><i /> PDF.js 텍스트 추출</span>
            <button className="ocr-primary" onClick={() => inputRef.current?.click()}>PDF 업로드</button>
          </div>
        </header>

        <input ref={inputRef} hidden type="file" accept="application/pdf" onChange={(e) => loadPdf(e.target.files?.[0])} />

        <div className="ocr-filebar">
          <div className="file-identity">
            <span className="pdf-badge">PDF</span>
            <span><strong>{fileName}</strong><small>{pdf ? `${pdf.numPages}페이지 · 텍스트 추출 완료` : '최대 50MB의 PDF 파일'}</small></span>
          </div>
          {pdf && <button className="ghost-button" onClick={() => inputRef.current?.click()}>파일 변경</button>}
        </div>

        <section className="ocr-editor" onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); loadPdf(e.dataTransfer.files?.[0]); }}>
          <aside className="pages-panel">
            <div className="panel-heading"><span>페이지</span><b>{pdf?.numPages || 0}</b></div>
            <div className="thumb-list">
              {pdf ? Array.from({ length: pdf.numPages }, (_, index) => (
                <button key={index} className={`page-thumb ${pageNumber === index + 1 ? 'active' : ''}`} onClick={() => setPageNumber(index + 1)}>
                  <span className="thumb-paper"><PdfCanvas pdf={pdf} pageNumber={index + 1} scale={0.22} thumbnail /></span>
                  <span>{index + 1} 페이지</span>
                </button>
              )) : <div className="empty-pages">PDF를 업로드하면<br />페이지별로 표시됩니다.</div>}

            </div>
          </aside>

          <div className="preview-panel">
            <div className="preview-toolbar">
              <div>
                <button disabled={!pdf || pageNumber === 1} onClick={() => setPageNumber((p) => p - 1)} aria-label="이전 페이지">‹</button>
                <span>{pdf ? `${pageNumber} / ${pdf.numPages}` : '0 / 0'}</span>
                <button disabled={!pdf || pageNumber === pdf?.numPages} onClick={() => setPageNumber((p) => p + 1)} aria-label="다음 페이지">›</button>
              </div>
              <strong>문서 미리보기</strong>
              <div>
                <button onClick={() => setZoom((z) => Math.max(0.55, z - 0.15))} aria-label="축소">−</button>
                <span>{Math.round(zoom * 100)}%</span>
                <button onClick={() => setZoom((z) => Math.min(2, z + 0.15))} aria-label="확대">＋</button>
              </div>
            </div>
            <div className="preview-stage">
              {loading ? <div className="loader"><span />PDF를 분석하고 있습니다...</div> : pdf ? <PdfCanvas pdf={pdf} pageNumber={pageNumber} scale={zoom} /> : (
                <button className="dropzone" onClick={() => inputRef.current?.click()}>
                  <span className="drop-icon">⇧</span><strong>PDF를 여기에 놓아주세요</strong><small>또는 클릭해서 파일을 선택하세요</small>
                </button>
              )}
              {error && <div className="ocr-error">{error}</div>}
            </div>
          </div>

          <aside className="text-panel">
            <div className="text-tabs"><button className="active">텍스트 보기</button><button>구조화</button><button>표</button></div>
            <div className="text-header">
              <div><span>추출된 텍스트</span><small>{pdf ? `${pageNumber} 페이지` : '대기 중'}</small></div>
              <button disabled={!pdf} onClick={downloadText} title="텍스트 다운로드">⇩</button>
            </div>
            <div className="text-meta"><span>{currentText.length.toLocaleString()}자</span><span>텍스트 레이어</span></div>
            <div className={`extracted-copy ${!pdf ? 'placeholder' : ''}`}>
              {pdf ? (currentText || '이 페이지에는 추출 가능한 텍스트가 없습니다.') : 'PDF를 업로드하면 페이지별 추출 텍스트가 여기에 표시됩니다.'}
            </div>
            <div className="text-note"><b>i</b><p>현재는 PDF 내부의 문자 레이어를 추출합니다. 스캔 이미지 PDF는 별도의 OCR 엔진 연결이 필요합니다.</p></div>
          </aside>
        </section>
      </main>
    </div>
  );
}
