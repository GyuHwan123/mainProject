import { useEffect, useRef, useState } from 'react';
import * as pdfjsLib from 'pdfjs-dist';
import pdfWorker from 'pdfjs-dist/build/pdf.worker.min.mjs?url';
import Sidebar from '../components/Sidebar';

pdfjsLib.GlobalWorkerOptions.workerSrc = pdfWorker;

const Icon = ({ children, size = 20 }) => (
  <span className="ocr-icon" style={{ width: size, height: size }}>{children}</span>
);

function PdfCanvas({ pdf, pageNumber, scale = 1.25, thumbnail = false }) {
  const canvasRef = useRef(null);

  useEffect(() => {
    if (!pdf) return undefined;
    let cancelled = false;
    let renderTask;
    pdf.getPage(pageNumber).then((page) => {
      if (cancelled) return;
      const viewport = page.getViewport({ scale });
      const canvas = canvasRef.current;
      const context = canvas.getContext('2d');
      canvas.width = viewport.width;
      canvas.height = viewport.height;
      renderTask = page.render({ canvasContext: context, viewport });
    });
    return () => {
      cancelled = true;
      renderTask?.cancel();
    };
  }, [pdf, pageNumber, scale]);

  return <canvas ref={canvasRef} className={thumbnail ? 'pdf-thumb-canvas' : 'pdf-main-canvas'} />;
}

export default function OCRPage() {
  const [pdf, setPdf] = useState(null);
  const [fileName, setFileName] = useState('문서를 선택해 주세요');
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
      const texts = await Promise.all(Array.from({ length: document.numPages }, async (_, index) => {
        const page = await document.getPage(index + 1);
        const content = await page.getTextContent();
        return content.items.map((item) => item.str).join(' ').replace(/\s+/g, ' ').trim();
      }));
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

  return (
    <div className="app-shell ocr-app-shell">
      <Sidebar />
      <main className="ocr-workspace">
        <header className="ocr-header">
          <div>
            <p className="eyebrow">DOCUMENT WORKSPACE</p>
            <h1>PDF 텍스트 추출</h1>
          </div>
          <div className="ocr-header-actions">
            <span className="extract-method"><i /> PDF.js 기본 추출</span>
            <button className="ocr-primary" onClick={() => inputRef.current?.click()}>＋ PDF 업로드</button>
            <input ref={inputRef} hidden type="file" accept="application/pdf" onChange={(e) => loadPdf(e.target.files?.[0])} />
          </div>
        </header>

        <div className="ocr-filebar">
          <div className="file-identity"><span className="pdf-badge">PDF</span><div><strong>{fileName}</strong><small>{pdf ? `${pdf.numPages}페이지 · 텍스트 추출 완료` : '최대 50MB의 PDF 파일'}</small></div></div>
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
              )) : <div className="empty-pages">업로드 후 페이지별로<br />나누어 표시됩니다.</div>}
            </div>
          </aside>

          <div className="preview-panel">
            <div className="preview-toolbar">
              <div><button disabled={!pdf || pageNumber === 1} onClick={() => setPageNumber((p) => p - 1)}>‹</button><span>{pdf ? `${pageNumber} / ${pdf.numPages}` : '0 / 0'}</span><button disabled={!pdf || pageNumber === pdf.numPages} onClick={() => setPageNumber((p) => p + 1)}>›</button></div>
              <span>문서 미리보기</span>
              <div><button onClick={() => setZoom((z) => Math.max(.55, z - .15))}>−</button><span>{Math.round(zoom * 100)}%</span><button onClick={() => setZoom((z) => Math.min(2, z + .15))}>＋</button></div>
            </div>
            <div className="preview-stage">
              {loading ? <div className="loader"><span />PDF를 분석하고 있습니다...</div> : pdf ? <PdfCanvas pdf={pdf} pageNumber={pageNumber} scale={zoom} /> : (
                <button className="dropzone" onClick={() => inputRef.current?.click()}><span className="drop-icon">↥</span><strong>PDF를 여기에 끌어놓으세요</strong><small>또는 클릭해서 파일을 선택하세요</small></button>
              )}
              {error && <div className="ocr-error">{error}</div>}
            </div>
          </div>

          <aside className="text-panel">
            <div className="text-header"><div><span>추출된 텍스트</span><small>{pdf ? `${pageNumber} 페이지` : '대기 중'}</small></div><button disabled={!pdf} onClick={downloadText} title="텍스트 파일 다운로드">↓</button></div>
            <div className="text-meta"><span>{(pageTexts[pageNumber - 1] || '').length.toLocaleString()}자</span><span>텍스트 레이어</span></div>
            <div className={`extracted-copy ${!pdf ? 'placeholder' : ''}`}>
              {pdf ? (pageTexts[pageNumber - 1] || '이 페이지에는 추출 가능한 텍스트 레이어가 없습니다.') : 'PDF를 업로드하면 페이지별로 추출된 텍스트가 여기에 표시됩니다.'}
            </div>
            <div className="text-note"><b>i</b><p>현재는 PDF 내부의 문자 레이어를 추출합니다. 스캔 이미지 PDF는 별도 OCR 엔진 연결이 필요합니다.</p></div>
          </aside>
        </section>
      </main>
    </div>
  );
}
