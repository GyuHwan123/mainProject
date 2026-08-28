import { Component, useEffect, useMemo, useRef, useState } from 'react';
import * as pdfjsLib from 'pdfjs-dist';
import pdfWorker from 'pdfjs-dist/build/pdf.worker.min.mjs?url';
import { IoBookmarkOutline, IoCloseOutline, IoTrashOutline } from 'react-icons/io5';
import { RiFileUploadLine } from 'react-icons/ri';
import Sidebar from '../components/Sidebar';
import apiClient from '../api/client';
import { getAppUser } from '../features/appSession';
import '../style/ChatPage.scss';

pdfjsLib.GlobalWorkerOptions.workerSrc = pdfWorker;
const SCRAPBOOK_KEY = 'docunex_knowledge_scrapbook';
const ACTIVE_CHAT_SESSION_KEY = 'docunex_active_chat_session';
const CHAT_STATE_KEY_PREFIX = 'docunex_chat_state:';
const HISTORY_PAGE_SIZE = 5;
const COMPANY_DOCUMENT_ID_PATTERN = /^(?:HR-00[1-5]|GA-00[1-4]|IS-00[1-2]|SH-00[1-4]|ER-00[1-3])$/;

function HistoryPagination({ page, totalItems, onChange, label }) {
  const totalPages = Math.ceil(totalItems / HISTORY_PAGE_SIZE);
  if (totalPages <= 1) return null;
  return <nav className="history-pagination" aria-label={`${label} 페이지`}>
    <button type="button" disabled={page <= 1} onClick={() => onChange(page - 1)} aria-label="이전 페이지">‹</button>
    {Array.from({ length: totalPages }, (_, index) => index + 1).map((number) => <button type="button" key={number} className={page === number ? 'active' : ''} aria-current={page === number ? 'page' : undefined} onClick={() => onChange(number)}>{number}</button>)}
    <button type="button" disabled={page >= totalPages} onClick={() => onChange(page + 1)} aria-label="다음 페이지">›</button>
  </nav>;
}

function DeleteConfirmDialog({ request, deleting, error, onCancel, onConfirm }) {
  if (!request) return null;
  const isAll = request.mode === 'all';
  const target = request.target === 'rag' ? '기록보관함' : '채팅이력';
  const title = isAll ? `${target}을 전체 삭제하시겠습니까?` : '이 기록을 삭제하시겠습니까?';
  return <div className="delete-confirm-backdrop" role="presentation" onMouseDown={(event) => { if (!deleting && event.target === event.currentTarget) onCancel(); }}>
    <section className="delete-confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="delete-confirm-title" aria-describedby="delete-confirm-description">
      <span className="delete-confirm-icon"><IoTrashOutline /></span>
      <div><small>{target} {isAll ? '전체삭제' : '기록 삭제'}</small><h2 id="delete-confirm-title">{title}</h2><p id="delete-confirm-description">삭제한 기록은 복구할 수 없습니다.</p></div>
      {error && <p className="delete-confirm-error" role="alert">{error}</p>}
      <footer><button type="button" className="cancel-delete" disabled={deleting} onClick={onCancel}>취소</button><button type="button" className="confirm-delete" disabled={deleting} onClick={onConfirm}>{deleting ? '삭제 중...' : (isAll ? '전체삭제' : '삭제')}</button></footer>
    </section>
  </div>;
}

class ChatErrorBoundary extends Component {
  constructor(props) { super(props); this.state = { failed: false, message: '' }; }
  static getDerivedStateFromError(error) { return { failed: true, message: error?.message || '알 수 없는 렌더링 오류' }; }
  componentDidCatch(error, info) { console.error('Chat 화면 렌더링 오류', error, info); }
  render() {
    if (this.state.failed) return <div className="chat-render-error"><strong>문서 화면을 불러오지 못했습니다.</strong><p>{this.state.message}</p><button onClick={() => window.location.reload()}>화면 다시 불러오기</button></div>;
    return this.props.children;
  }
}

function bboxPoints(value) {
  let parsed = value;
  if (typeof parsed === 'string') {
    try { parsed = JSON.parse(parsed); } catch { return []; }
  }
  if (Array.isArray(parsed)) {
    if (parsed.length >= 4 && parsed.slice(0, 4).every((value) => Number.isFinite(Number(value)))) {
      return [[Number(parsed[0]), Number(parsed[1])], [Number(parsed[2]), Number(parsed[3])]];
    }
    return parsed.filter((point) => Array.isArray(point) && point.length >= 2);
  }
  if (parsed && typeof parsed === 'object') {
    if (parsed.bbox !== undefined) return bboxPoints(parsed.bbox);
    const left = Number(parsed.left ?? parsed.x); const top = Number(parsed.top ?? parsed.y);
    const right = Number(parsed.right ?? (Number.isFinite(left) ? left + Number(parsed.width || 0) : NaN));
    const bottom = Number(parsed.bottom ?? (Number.isFinite(top) ? top + Number(parsed.height || 0) : NaN));
    if ([left, top, right, bottom].every(Number.isFinite)) return [[left, top], [right, bottom]];
  }
  return [];
}

function normalizeEvidenceText(value) {
  return String(value || '').replace(/\s+/g, ' ').trim();
}

function findEvidenceItemIndices(items, sourceContent) {
  const normalizedSource = normalizeEvidenceText(sourceContent);
  if (!normalizedSource) return [];

  const characters = [];
  items.forEach((item, itemIndex) => {
    for (const character of String(item?.str || '')) characters.push({ character, itemIndex });
    if (item?.hasEOL) characters.push({ character: ' ', itemIndex });
  });

  let normalizedPage = '';
  const itemIndexByCharacter = [];
  let pendingSpaceItem = null;
  characters.forEach(({ character, itemIndex }) => {
    if (/\s/.test(character)) {
      if (normalizedPage && !normalizedPage.endsWith(' ')) pendingSpaceItem = itemIndex;
      return;
    }
    if (pendingSpaceItem !== null) {
      normalizedPage += ' ';
      itemIndexByCharacter.push(pendingSpaceItem);
      pendingSpaceItem = null;
    }
    normalizedPage += character;
    itemIndexByCharacter.push(itemIndex);
  });

  const candidates = [normalizedSource];
  [240, 160, 100, 60, 40].forEach((length) => {
    if (normalizedSource.length > length) candidates.push(normalizedSource.slice(0, length).trim());
  });
  const match = candidates.find((candidate, index) => candidate.length >= (index === 0 ? 24 : 40) && normalizedPage.includes(candidate));
  if (!match) return [];

  const start = normalizedPage.indexOf(match);
  return [...new Set(itemIndexByCharacter.slice(start, start + match.length))];
}

function PdfEvidencePage({ pdf, pageNumber, bbox, privacyBoxes = [], sourceContent = '' }) {
  const canvasRef = useRef(null);
  const [pageSize, setPageSize] = useState({ width: 0, height: 0, naturalWidth: 0, naturalHeight: 0, scale: 1 });
  const [evidenceHighlights, setEvidenceHighlights] = useState([]);

  useEffect(() => {
    let active = true;
    let renderTask;
    pdf.getPage(pageNumber).then((page) => {
      if (!active || !canvasRef.current) return;
      const naturalViewport = page.getViewport({ scale: 1 });
      const viewport = page.getViewport({ scale: 1.05 });
      const canvas = canvasRef.current;
      canvas.width = viewport.width;
      canvas.height = viewport.height;
      setPageSize({ width: viewport.width, height: viewport.height, naturalWidth: naturalViewport.width, naturalHeight: naturalViewport.height, scale: 1.05 });
      renderTask = page.render({ canvasContext: canvas.getContext('2d'), viewport });
      return renderTask.promise;
    }).catch(() => {});
    return () => { active = false; try { renderTask?.cancel(); } catch { /* 이미 종료된 렌더 작업 */ } };
  }, [pdf, pageNumber]);

  useEffect(() => {
    let active = true;
    setEvidenceHighlights([]);
    if (!sourceContent) return () => { active = false; };

    pdf.getPage(pageNumber).then(async (page) => {
      const viewport = page.getViewport({ scale: 1.05 });
      const textContent = await page.getTextContent();
      if (!active) return;
      const items = Array.isArray(textContent?.items) ? textContent.items : [];
      const matchedIndices = findEvidenceItemIndices(items, sourceContent);
      const highlights = matchedIndices.map((itemIndex) => {
        const item = items[itemIndex];
        if (!item?.transform) return null;
        const transform = pdfjsLib.Util.transform(viewport.transform, item.transform);
        const fontHeight = Math.hypot(transform[2], transform[3]);
        const width = Math.max(2, Number(item.width || 0) * viewport.scale);
        if (!fontHeight || !width) return null;
        return {
          left: transform[4],
          top: transform[5] - fontHeight,
          width,
          height: fontHeight,
          transform: `rotate(${Math.atan2(transform[1], transform[0])}rad)`,
        };
      }).filter(Boolean);
      setEvidenceHighlights(highlights);
    }).catch(() => {});

    return () => { active = false; };
  }, [pdf, pageNumber, sourceContent]);

  const boxStyle = (() => {
    const points = bboxPoints(bbox);
    if (!points.length || !pageSize.width) return null;
    const xs = points.map((point) => Number(point[0])).filter(Number.isFinite);
    const ys = points.map((point) => Number(point[1])).filter(Number.isFinite);
    if (!xs.length || !ys.length) return null;
    const normalized = Math.max(...xs) <= 1.5 && Math.max(...ys) <= 1.5;
    const scaleX = normalized ? pageSize.width : pageSize.scale;
    const scaleY = normalized ? pageSize.height : pageSize.scale;
    return { left: Math.min(...xs) * scaleX, top: Math.min(...ys) * scaleY, width: Math.max(3, (Math.max(...xs) - Math.min(...xs)) * scaleX), height: Math.max(3, (Math.max(...ys) - Math.min(...ys)) * scaleY) };
  })();
  const privacyStyles = (Array.isArray(privacyBoxes) ? privacyBoxes : []).map((box) => {
    const points = bboxPoints(box); const xs = points.map((point) => Number(point[0])).filter(Number.isFinite); const ys = points.map((point) => Number(point[1])).filter(Number.isFinite);
    return xs.length && ys.length ? { left: Math.min(...xs) * pageSize.scale, top: Math.min(...ys) * pageSize.scale, width: (Math.max(...xs) - Math.min(...xs)) * pageSize.scale, height: (Math.max(...ys) - Math.min(...ys)) * pageSize.scale } : null;
  }).filter(Boolean);
  return <div className="evidence-pdf-page"><canvas ref={canvasRef} /><div className="rag-evidence-highlight-layer" aria-hidden="true">{evidenceHighlights.map((style, index) => <span className="rag-evidence-highlight" style={style} key={index} />)}</div>{boxStyle && <span className="evidence-bbox" style={boxStyle} />}{privacyStyles.map((style, index) => <span className="privacy-mask" style={style} key={index}>보호됨</span>)}</div>;
}

function SpreadsheetEvidencePage({ page, bbox }) {
  const rows = Array.isArray(page?.rows) ? page.rows : [];
  const columnCount = Math.max(0, ...rows.map((row) => Array.isArray(row) ? row.length : 0));
  const points = bboxPoints(bbox);
  const xs = points.map((point) => Number(point[0])).filter(Number.isFinite);
  const ys = points.map((point) => Number(point[1])).filter(Number.isFinite);
  const selectedColumnStart = xs.length ? Math.floor(Math.min(...xs)) + 1 : null;
  const selectedColumnEnd = xs.length ? Math.ceil(Math.max(...xs)) : null;
  const selectedRowStart = ys.length ? Math.floor(Math.min(...ys)) + 1 : null;
  const selectedRowEnd = ys.length ? Math.ceil(Math.max(...ys)) : null;
  const columnLabel = (index) => {
    let value = index + 1;
    let label = '';
    while (value > 0) {
      value -= 1;
      label = String.fromCharCode(65 + (value % 26)) + label;
      value = Math.floor(value / 26);
    }
    return label;
  };
  return <div className="evidence-spreadsheet">
    <div className="evidence-sheet-name">{page?.sheet_name || 'Sheet'}</div>
    <table>
      <thead><tr><th className="sheet-corner" />{Array.from({ length: columnCount }, (_, index) => <th key={index}>{columnLabel(index)}</th>)}</tr></thead>
      <tbody>{rows.map((row, rowIndex) => <tr key={rowIndex}>
        <th>{rowIndex + 1}</th>
        {Array.from({ length: columnCount }, (_, columnIndex) => {
          const rowNumber = rowIndex + 1;
          const columnNumber = columnIndex + 1;
          const selected = rowNumber >= selectedRowStart && rowNumber <= selectedRowEnd && columnNumber >= selectedColumnStart && columnNumber <= selectedColumnEnd;
          return <td key={columnIndex} className={selected ? 'selected' : ''}>{row?.[columnIndex] ?? ''}</td>;
        })}
      </tr>)}</tbody>
    </table>
  </div>;
}

function DocumentSummaryPreview({ document }) {
  if (!document) return <div className="document-summary-empty">
    <strong>요약할 문서를 선택해 주세요.</strong>
    <p>RAG 문서를 선택하면 AI 문서 요약을 확인할 수 있습니다.</p>
  </div>;

  return <section className="document-summary-preview" aria-labelledby="document-summary-title">
    <header>
      <div><small>DOCUMENT SUMMARY</small><h2 id="document-summary-title">AI 문서 요약</h2></div>
      <span title={document.name}>{document.name}</span>
    </header>
    <div className="document-summary-placeholder">
      <strong>문서 요약을 준비할 수 있습니다.</strong>
      <p>현재는 요약 API가 연결되지 않아 문서 요약 내용이 표시되지 않습니다.</p>
    </div>
  </section>;
}

function EvidencePreview({ source, onUpload, uploading }) {
  const [preview, setPreview] = useState({ type: '', url: '', pdf: null, pageCount: 0, width: 0, height: 0, scale: 1 });
  const [privacyPages, setPrivacyPages] = useState([]);
  const [currentPage, setCurrentPage] = useState(1);
  const bbox = Number(source?.pageNumber || 1) === currentPage ? source?.bbox : null;
  const isCompanyDocument = COMPANY_DOCUMENT_ID_PATTERN.test(source?.documentId || '');

  useEffect(() => {
    setCurrentPage(Math.max(1, Number(source?.pageNumber) || 1));
  }, [source?.documentId, source?.pageNumber]);

  useEffect(() => {
    let active = true;
    let objectUrl = '';
    let pdfDocument;
    let loadingTask;
    const load = async () => {
      if (!source?.documentId) { setPreview({ type: '', url: '', pdf: null, pageCount: 0, width: 0, height: 0, scale: 1 }); return; }
      setPreview({ type: 'loading', url: '', pdf: null, pageCount: 0, width: 0, height: 0, scale: 1 });
      const [{ data: blob }, { data: privacy }] = await Promise.all([
        apiClient.get(isCompanyDocument
          ? `/rag/company-documents/${encodeURIComponent(source.documentId)}/file`
          : `/ocr/documents/${source.documentId}/file`, { responseType: 'blob', timeout: 60000 }),
        isCompanyDocument
          ? Promise.resolve({ data: [] })
          : apiClient.get(`/ocr/documents/${source.documentId}/privacy-boxes`),
      ]);
      if (active) setPrivacyPages(Array.isArray(privacy) ? privacy : []);
      const name = source.source || '';
      if (/\.(png|jpe?g|webp|bmp)$/i.test(name)) {
        objectUrl = URL.createObjectURL(blob);
        if (active) setPreview({ type: 'image', url: objectUrl, pdf: null, pageCount: 1, width: 0, height: 0, scale: 1 });
        return;
      }
      let pdfData;
      if (/\.docx$/i.test(name)) {
        const formData = new FormData(); formData.append('file', blob, name);
        const response = await apiClient.post('/ocr/docx-preview', formData, { responseType: 'arraybuffer', timeout: 120000 });
        pdfData = response.data;
      } else if (/\.(xlsx|xlsm)$/i.test(name)) {
        const formData = new FormData(); formData.append('file', blob, name);
        const response = await apiClient.post('/ocr/spreadsheet-preview', formData, { timeout: 120000 });
        const pages = Array.isArray(response.data?.pages) ? response.data.pages : [];
        if (active) setPreview({ type: 'spreadsheet', url: '', pdf: null, pages, pageCount: pages.length, width: 0, height: 0, scale: 1 });
        return;
      } else if (/\.pdf$/i.test(name)) pdfData = await blob.arrayBuffer();
      else { if (active) setPreview({ type: 'unsupported', url: '', pdf: null, pageCount: 0, width: 0, height: 0, scale: 1 }); return; }
      loadingTask = pdfjsLib.getDocument({ data: pdfData });
      pdfDocument = await loadingTask.promise;
      if (active) setPreview({ type: 'pdf', url: '', pdf: pdfDocument, pageCount: pdfDocument.numPages, width: 0, height: 0, scale: 1 });
    };
    load().catch(() => active && setPreview({ type: 'error', url: '', pdf: null, pageCount: 0, width: 0, height: 0, scale: 1 }));
    return () => {
      active = false;
      if (typeof pdfDocument?.destroy === 'function') {
        Promise.resolve(pdfDocument.destroy()).catch(() => {});
      } else if (typeof loadingTask?.destroy === 'function') {
        Promise.resolve(loadingTask.destroy()).catch(() => {});
      }
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [isCompanyDocument, source?.documentId, source?.source]);

  const boxStyle = (() => {
    const points = bboxPoints(bbox);
    if (!points.length || !preview.width || !preview.height) return null;
    const xs = points.map((point) => Number(point[0])).filter(Number.isFinite);
    const ys = points.map((point) => Number(point[1])).filter(Number.isFinite);
    if (!xs.length || !ys.length) return null;
    return { left: Math.min(...xs) * preview.scale, top: Math.min(...ys) * preview.scale, width: Math.max(3, (Math.max(...xs) - Math.min(...xs)) * preview.scale), height: Math.max(3, (Math.max(...ys) - Math.min(...ys)) * preview.scale) };
  })();
  const safePrivacyPages = Array.isArray(privacyPages) ? privacyPages : [];
  const imagePrivacyStyles = (safePrivacyPages.find((page) => page.page === 1)?.boxes || []).map((box) => {
    const points = bboxPoints(box); const xs = points.map((point) => Number(point[0])).filter(Number.isFinite); const ys = points.map((point) => Number(point[1])).filter(Number.isFinite);
    return xs.length && ys.length ? { left: Math.min(...xs) * preview.scale, top: Math.min(...ys) * preview.scale, width: (Math.max(...xs) - Math.min(...xs)) * preview.scale, height: (Math.max(...ys) - Math.min(...ys)) * preview.scale } : null;
  }).filter(Boolean);

  if (!source) return <button type="button" className="rag-first-upload" disabled={uploading} onClick={onUpload} onDragOver={(event) => { event.preventDefault(); event.dataTransfer.dropEffect = 'copy'; }} onDrop={(event) => { event.preventDefault(); if (!uploading) onUpload?.([...event.dataTransfer.files]); }}><RiFileUploadLine /><strong>{uploading ? 'OCR · RAG 처리 중...' : 'RAG 문서를 업로드하세요'}</strong><p>파일을 이곳으로 드래그하거나 클릭해서 선택하세요.</p><small>PDF · DOCX · 이미지 · XLSX · TXT</small></button>;
  const pageCount = Math.max(1, preview.pageCount || 1);
  return <div className="evidence-preview"><div className="evidence-preview-label"><span>{source.source}</span><div className="evidence-page-controls"><button disabled={currentPage <= 1} onClick={() => setCurrentPage((page) => page - 1)}>‹</button><b>{currentPage} / {pageCount}</b><button disabled={currentPage >= pageCount} onClick={() => setCurrentPage((page) => page + 1)}>›</button></div></div><div className="evidence-preview-body">
    {['pdf', 'spreadsheet'].includes(preview.type) && <aside className="evidence-page-list">{Array.from({ length: pageCount }, (_, index) => <button key={index + 1} className={currentPage === index + 1 ? 'active' : ''} onClick={() => setCurrentPage(index + 1)}><span>{index + 1}</span><small>{preview.type === 'spreadsheet' ? 'SHEET' : 'PAGE'}</small></button>)}</aside>}
    <div className="evidence-document-stage">
    {preview.type === 'loading' && <div className="evidence-preview-loading"><i /><span>문서 미리보기를 불러오는 중...</span></div>}
    {preview.type === 'image' && <img src={preview.url} alt="근거 문서" onLoad={(event) => { const image = event.currentTarget; const scale = image.clientWidth / image.naturalWidth; setPreview((value) => ({ ...value, width: image.naturalWidth, height: image.naturalHeight, scale })); }} />}
    {preview.type === 'pdf' && preview.pdf && <PdfEvidencePage pdf={preview.pdf} pageNumber={currentPage} bbox={bbox} privacyBoxes={safePrivacyPages.find((page) => page.page === currentPage)?.boxes || []} sourceContent={Number(source?.pageNumber || 1) === currentPage ? source?.content : ''} />}
    {preview.type === 'spreadsheet' && <SpreadsheetEvidencePage page={preview.pages?.[currentPage - 1]} bbox={bbox} />}
    {preview.type === 'image' && boxStyle && <span className="evidence-bbox" style={boxStyle} />}
    {preview.type === 'image' && imagePrivacyStyles.map((style, index) => <span className="privacy-mask" style={style} key={index}>보호됨</span>)}
    {['unsupported', 'error'].includes(preview.type) && <div className="evidence-preview-empty"><strong>{isCompanyDocument ? '기업 공용문서 원본을 표시할 수 없습니다' : '미리보기를 표시할 수 없습니다'}</strong>{!isCompanyDocument && <button onClick={() => { window.location.href = `/ocr?document=${encodeURIComponent(source.documentId)}&page=${source.pageNumber}&bbox=${encodeURIComponent(JSON.stringify(source.bbox))}`; }}>OCR 원문에서 보기</button>}</div>}
    </div>
  </div>{isCompanyDocument && source.content && <aside className="company-evidence-text"><strong>근거 텍스트</strong><p>{source.content}</p></aside>}</div>;
}

function ChatPageContent() {
  const appUser = getAppUser();
  const chatStateKey = `${CHAT_STATE_KEY_PREFIX}${appUser.email || 'anonymous'}`;
  const restoredChatState = useMemo(() => {
    try { return JSON.parse(localStorage.getItem(chatStateKey) || '{}'); } catch { return {}; }
  }, [chatStateKey]);
  const isDeveloper = ['DEVELOPER', 'ADMIN'].includes(appUser.role) || appUser.email === 'developer@docunex.com';
  const [documents, setDocuments] = useState([]);
  const [indexingId, setIndexingId] = useState(null);
  const [ragError, setRagError] = useState('');
  const [sessions, setSessions] = useState([]);
  const [sessionsLoaded, setSessionsLoaded] = useState(false);
  const [documentsLoaded, setDocumentsLoaded] = useState(false);
  const [ragHistoryPage, setRagHistoryPage] = useState(1);
  const [chatHistoryPage, setChatHistoryPage] = useState(1);
  const [deleteRequest, setDeleteRequest] = useState(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState('');
  const [activeSessionId, setActiveSessionId] = useState(restoredChatState.activeSessionId ?? null);
  const [activeId, setActiveId] = useState(restoredChatState.activeId ?? null);
  const [messages, setMessages] = useState(() => Array.isArray(restoredChatState.messages) && restoredChatState.messages.length
    ? restoredChatState.messages
    : [{ role: 'assistant', text: '안녕하세요. 문서를 업로드한 뒤 궁금한 내용을 질문해 주세요. 문서에서 관련 근거를 찾아 답변해 드립니다.' }]);
  const [query, setQuery] = useState(restoredChatState.query || '');
  const [sources, setSources] = useState(() => Array.isArray(restoredChatState.sources) ? restoredChatState.sources : []);
  const [selectedSource, setSelectedSource] = useState(restoredChatState.selectedSource || null);
  const [busy, setBusy] = useState(false);
  const [uploadMode, setUploadMode] = useState(false);
  const [documentViewMode, setDocumentViewMode] = useState('viewer');
  const [scrapbookOpen, setScrapbookOpen] = useState(false);
  const [scrapSaving, setScrapSaving] = useState(false);
  const [scrapError, setScrapError] = useState('');
  const [evidenceFlash, setEvidenceFlash] = useState(false);
  const [modelConfig, setModelConfig] = useState({ model: 'Baseline LLM', embedding_model: 'Baseline Embedding', ready: false });
  const [evaluationDataset, setEvaluationDataset] = useState(null);
  const [evaluationResult, setEvaluationResult] = useState(() => {
    try {
      const saved = localStorage.getItem('pic_to_text_rag_evaluation_latest');
      return saved ? JSON.parse(saved) : null;
    } catch {
      return null;
    }
  });
  const [evaluationStatus, setEvaluationStatus] = useState('대기');
  const [evaluationRunning, setEvaluationRunning] = useState(false);
  const [evaluationError, setEvaluationError] = useState('');
  const [scrapbook, setScrapbook] = useState(() => {
    try { return JSON.parse(localStorage.getItem(SCRAPBOOK_KEY) || '[]'); } catch { return []; }
  });
  const fileRef = useRef(null);
  const evaluationFileRef = useRef(null);
  const evaluationRunningRef = useRef(false);
  const restorationAttemptedRef = useRef(false);
  const endRef = useRef(null);
  const activeDoc = documents.find((item) => item.id === activeId);
  const previewSource = selectedSource || (activeDoc ? {
    documentId: activeDoc.documentId,
    source: activeDoc.name,
    pageNumber: 1,
    bbox: null,
    isDocumentPreview: true,
  } : null);
  const totalChunks = useMemo(() => documents.reduce((sum, item) => sum + (item.chunkCount || 0), 0), [documents]);
  const pagedDocuments = useMemo(() => documents.slice((ragHistoryPage - 1) * HISTORY_PAGE_SIZE, ragHistoryPage * HISTORY_PAGE_SIZE), [documents, ragHistoryPage]);
  const pagedSessions = useMemo(() => sessions.slice((chatHistoryPage - 1) * HISTORY_PAGE_SIZE, chatHistoryPage * HISTORY_PAGE_SIZE), [sessions, chatHistoryPage]);

  useEffect(() => {
    setRagHistoryPage((page) => Math.min(page, Math.max(1, Math.ceil(documents.length / HISTORY_PAGE_SIZE))));
  }, [documents.length]);

  useEffect(() => {
    setChatHistoryPage((page) => Math.min(page, Math.max(1, Math.ceil(sessions.length / HISTORY_PAGE_SIZE))));
  }, [sessions.length]);

  useEffect(() => {
    if (!activeId) setDocumentViewMode('viewer');
  }, [activeId]);

  useEffect(() => {
    localStorage.setItem(SCRAPBOOK_KEY, JSON.stringify(scrapbook));
  }, [scrapbook]);

  useEffect(() => {
    if (!localStorage.getItem('pic_to_text_token')) return;
    localStorage.setItem(chatStateKey, JSON.stringify({
      messages, activeSessionId, activeId, sources, selectedSource, query,
    }));
    if (activeSessionId) localStorage.setItem(ACTIVE_CHAT_SESSION_KEY, String(activeSessionId));
    else localStorage.removeItem(ACTIVE_CHAT_SESSION_KEY);
  }, [chatStateKey, messages, activeSessionId, activeId, sources, selectedSource, query]);

  useEffect(() => {
    apiClient.get('/chatbot/status').then(({ data }) => setModelConfig(data)).catch(() => {});
  }, []);

  const refreshSessions = () => apiClient.get('/chatbot/sessions')
    .then(({ data }) => setSessions(data || []))
    .catch(() => {})
    .finally(() => setSessionsLoaded(true));

  useEffect(() => { refreshSessions(); }, []);

  useEffect(() => {
    if (!sessionsLoaded || !documentsLoaded || restorationAttemptedRef.current) return;
    restorationAttemptedRef.current = true;

    const savedSessionId = localStorage.getItem(ACTIVE_CHAT_SESSION_KEY);
    if (!savedSessionId) return;

    const savedSession = sessions.find(
        (session) => String(session.id) === String(savedSessionId)
    );

    if (savedSession) {
        openSession(savedSession, { restoreEvidence: true }).catch(() => {
        localStorage.removeItem(ACTIVE_CHAT_SESSION_KEY);
        });
    } else {
      setActiveSessionId(null);
      localStorage.removeItem(ACTIVE_CHAT_SESSION_KEY);
    }
    }, [sessions, sessionsLoaded, documentsLoaded]);


  const refreshRagDocuments = () => apiClient.get('/rag/documents').then(({ data }) => {
    const mapped = (data || []).map((item) => ({
      id: item.id,
      documentId: item.document_id,
      name: item.ocr_documents?.file_name || item.file_name || '문서',
      status: item.status,
      chunkCount: item.chunk_count || 0,
      createdAt: new Date(item.created_at),
    }));
    setDocuments(mapped);
    // Preserve the restored/explicitly selected document, but do not select
    // the first history item automatically.
    setActiveId((current) => mapped.some((item) => item.id === current) ? current : null);
  }).catch(() => {}).finally(() => setDocumentsLoaded(true));

  useEffect(() => { refreshRagDocuments(); }, []);

  useEffect(() => {
    apiClient.get('/chatbot/scraps').then(({ data }) => setScrapbook((data || []).map((item) => ({
      id: item.id,
      title: item.question,
      answer: item.answer,
      sourceCount: item.source_count || 0,
      documentName: item.document_name || '전체 RAG 문서',
      createdAt: item.created_at,
    })))).catch(() => {});
  }, []);

  const openSession = async (session, { restoreEvidence = false } = {}) => {
    const { data } = await apiClient.get(`/chatbot/sessions/${session.id}/messages`);
    const linkedDocument = documents.find((document) => document.documentId === session.document_id);
    setActiveId(linkedDocument?.id ?? null);
    setUploadMode(false);
    setActiveSessionId(session.id);
    localStorage.setItem(ACTIVE_CHAT_SESSION_KEY, session.id);
    setMessages((Array.isArray(data) ? data : []).map((item) => {
      const storedSources = Array.isArray(item.sources) ? item.sources : [];
      return {
        role: item.role,
        text: item.content,
        sourceCount: storedSources.length,
        sources: storedSources,
      };
    }));
    if (!restoreEvidence) {
      setSources([]);
      setSelectedSource(null);
    }
  };

  const startNewChat = () => {
    localStorage.removeItem(ACTIVE_CHAT_SESSION_KEY);
    setActiveSessionId(null);
    setMessages([{ role: 'assistant', text: '안녕하세요. 문서를 업로드한 후 궁금한 내용을 질문해 주세요. 문서에서 관련 근거를 찾아 답변해 드립니다.' }]);
    setQuery('');
    setSources([]);
    setSelectedSource(null);
  };

  const clearActiveDocument = () => {
    setActiveId(null);
    setSelectedSource(null);
    setSources([]);
    setEvidenceFlash(false);
    setUploadMode(false);
    setDocumentViewMode('viewer');
  };

  const uploadFiles = async (files) => {
    setRagError('');
    try {
      for (const file of files) {
        const formData = new FormData();
        formData.append('file', file);
        setIndexingId(file.name);
        const { data: extracted } = await apiClient.post('/ocr/upload?upload_origin=RAG', formData, {
          headers: { 'Content-Type': 'multipart/form-data' }, timeout: 300000,
        });
        const { data: indexed } = await apiClient.post(`/rag/documents/${extracted.document_id}/index`, null, { timeout: 300000 });
        setActiveId(indexed.id);
        setUploadMode(false);
        setSelectedSource(null);
      }
      setSources([]);
      await refreshRagDocuments();
    } catch (error) {
      setRagError(error.response?.data?.detail || 'OCR 또는 RAG 인덱싱에 실패했습니다.');
      throw error;
    } finally { setIndexingId(null); }
  };

  const ask = async () => {
    const question = query.trim();
    if (!question || busy) return;
    let relevant = [];
    let sessionId = activeSessionId;
    const recentHistory = messages.slice(-8).map((message) => ({
      role: message.role,
      content: message.text,
    }));
    const previousUserQuestion = [...messages].reverse().find((message) => message.role === 'user')?.text;
    const needsPreviousContext = /^(그|그럼|그러면|이건|저건|해당|방금|앞서)|누가라고|그 사람|그것|거기/.test(question);
    const searchQuery = previousUserQuestion && needsPreviousContext
      ? `이전 질문: ${previousUserQuestion}\n현재 후속 질문: ${question}`
      : question;
    setMessages((items) => [...items, { role: 'user', text: question }]);
    setQuery(''); setSources([]); setBusy(true);
    try {
      const { data: matches } = await apiClient.post('/rag/search', {
        query: searchQuery, rag_document_id: activeId || null, limit: modelConfig.top_k || 8,
      }, { timeout: 180000 });
      relevant = (matches || []).map((item) => ({
        id: item.id, content: item.content, source: item.source,
        index: item.chunk_index + 1, score: item.similarity,
        documentId: item.document_id, pageNumber: item.page_number, bbox: item.bbox,
      }));
      setSources(relevant);
      if (!sessionId) {
        try {
            const { data: session } = await apiClient.post('/chatbot/sessions', {
            title: question.slice(0, 120),
            document_id: activeDoc?.documentId ?? null,
            });

            sessionId = session.id;
            setActiveSessionId(sessionId);
            localStorage.setItem(ACTIVE_CHAT_SESSION_KEY, sessionId);
        } catch { /* 기록 저장 실패와 AI 답변 생성을 분리 */ }
        }
      if (sessionId) apiClient.post(`/chatbot/sessions/${sessionId}/messages`, {
        role: 'user', content: question, sources: [],
      }).catch(() => {});
      const context = relevant.map((chunk, index) => `[근거 ${index + 1} · ${chunk.source} · ${chunk.pageNumber}페이지 · Chunk ${chunk.index}] ${chunk.content}`).join('\n\n');
      const { data } = await apiClient.post('/chatbot/ask', {
        message: question,
        context,
        history: recentHistory,
      }, { timeout: 180000 });
      setMessages((items) => [...items, { role: 'assistant', text: data.reply, sourceCount: relevant.length, sources: relevant }]);
      if (sessionId) apiClient.post(`/chatbot/sessions/${sessionId}/messages`, {
        role: 'assistant', content: data.reply, model_name: data.model,
        sources: relevant.map(({ id, content, source, index, score, documentId, pageNumber, bbox }) => ({ id, content, source, index, score, documentId, pageNumber, bbox })),
      }).catch(() => {});
      refreshSessions();
    } catch (error) {
      const privacyDetail = error.response?.status === 403 && typeof error.response?.data?.detail === 'string'
        ? error.response.data.detail
        : '';
      const best = relevant.filter((item) => item.score > 0);
      const fallback = privacyDetail || (best.length
        ? `문서에서 다음과 같은 관련 내용을 찾았습니다.\n\n${best[0].content}\n\n현재 AI 응답에 실패하여 가장 관련도 높은 문서 근거를 대신 표시했습니다.`
        : `RAG 검색 또는 AI 응답에 실패했습니다. 문서가 RAG_READY 상태인지, Ollama에 ${modelConfig.embedding_model}와 ${modelConfig.model}이 설치되어 있는지 확인해 주세요.`);
      setMessages((items) => [...items, { role: 'assistant', text: fallback, sourceCount: best.length, sources: best }]);
      if (sessionId) {
        apiClient.post(`/chatbot/sessions/${sessionId}/messages`, {
          role: 'assistant', content: fallback, model_name: 'fallback',
          sources: best.map(({ id, content, source, index, score, documentId, pageNumber, bbox }) => ({ id, content, source, index, score, documentId, pageNumber, bbox })),
        }).then(refreshSessions).catch(() => {});
      }
    } finally { setBusy(false); setTimeout(() => endRef.current?.scrollIntoView({ behavior: 'smooth' }), 20); }
  };

  const requestDelete = (target, mode, id = null) => {
    setDeleteError('');
    setDeleteRequest({ target, mode, id });
  };

  const closeDeleteConfirm = () => {
    if (deleting) return;
    setDeleteRequest(null);
    setDeleteError('');
  };

  const confirmDelete = async () => {
    if (!deleteRequest || deleting) return;
    const { target, mode, id } = deleteRequest;
    const items = target === 'rag' ? documents : sessions;
    const ids = mode === 'all' ? items.map((item) => item.id) : [id];
    const endpoint = target === 'rag' ? '/rag/documents' : '/chatbot/sessions';
    setDeleting(true);
    setDeleteError('');
    try {
      const results = await Promise.allSettled(ids.map((itemId) => apiClient.delete(`${endpoint}/${itemId}`)));
      const deletedIds = ids.filter((_, index) => results[index].status === 'fulfilled');
      const failedCount = results.length - deletedIds.length;

      if (target === 'rag') {
        if (deletedIds.includes(activeId)) {
          setActiveId(null);
          setUploadMode(false);
          startNewChat();
        }
        if (deletedIds.length) setDocuments((current) => current.filter((item) => !deletedIds.includes(item.id)));
        if (mode === 'all' && !failedCount) setRagHistoryPage(1);
      } else {
        if (deletedIds.includes(activeSessionId)) startNewChat();
        if (deletedIds.length) setSessions((current) => current.filter((item) => !deletedIds.includes(item.id)));
        if (mode === 'all' && !failedCount) setChatHistoryPage(1);
      }

      if (failedCount) {
        setDeleteError(`${failedCount}개 기록을 삭제하지 못했습니다. 잠시 후 다시 시도해 주세요.`);
        return;
      }
      setDeleteRequest(null);
    } catch (error) {
      setDeleteError(error.response?.data?.detail || '기록을 삭제하지 못했습니다. 잠시 후 다시 시도해 주세요.');
    } finally {
      setDeleting(false);
    }
  };

  const saveToScrapbook = async (message, index) => {
    if (message.role !== 'assistant' || !message.text?.trim()) return;
    setScrapSaving(true); setScrapError('');
    const previousQuestion = [...messages.slice(0, index)].reverse().find((item) => item.role === 'user');
    const payload = {
      question: previousQuestion?.text || 'AI 답변', answer: message.text,
      document_name: activeDoc?.name || '전체 RAG 문서', source_count: message.sourceCount || 0,
      sources: message.sources || sources, model_name: modelConfig.model,
    };
    try {
      const { data } = await apiClient.post('/chatbot/scraps', payload);
      setScrapbook((items) => [{ id: data.id, title: data.question, answer: data.answer, sourceCount: data.source_count, documentName: data.document_name, createdAt: data.created_at }, ...items]);
      setScrapbookOpen(true);
    } catch (error) {
      setScrapError(error.response?.data?.detail || '지식 바구니 저장에 실패했습니다.');
    } finally { setScrapSaving(false); }
  };

  const removeScrap = async (id) => {
    await apiClient.delete(`/chatbot/scraps/${id}`);
    setScrapbook((items) => items.filter((saved) => saved.id !== id));
  };

  const loadEvaluationDataset = async (file) => {
    if (evaluationRunningRef.current) return;
    setEvaluationError(''); setEvaluationResult(null);
    try {
      if (!file || !/\.json$/i.test(file.name)) throw new Error('JSON 파일만 업로드할 수 있습니다.');
      const parsed = JSON.parse(await file.text());
      if (!Array.isArray(parsed.cases)) throw new Error('cases 배열이 필요합니다.');
      if (!parsed.cases.length) throw new Error('평가 문항이 없습니다.');
      parsed.cases.forEach((item, index) => {
        const label = item?.question_id || `${index + 1}번 문항`;
        if (typeof item?.question !== 'string' || !item.question.trim()) throw new Error(`${label}: question이 필요합니다.`);
        if (!Array.isArray(item.expected_documents)) throw new Error(`${label}: expected_documents 배열이 필요합니다.`);
        if (!Object.prototype.hasOwnProperty.call(item, 'expected_answer') || typeof item.expected_answer !== 'string') throw new Error(`${label}: expected_answer가 필요합니다.`);
        if (typeof item.answerable !== 'boolean') throw new Error(`${label}: answerable은 boolean이어야 합니다.`);
      });
      if (Number(parsed.question_count) !== parsed.cases.length) throw new Error('question_count와 cases 개수가 일치하지 않습니다.');
      setEvaluationDataset(parsed); setEvaluationStatus('대기');
    } catch (error) {
      setEvaluationDataset(null); setEvaluationStatus('대기');
      setEvaluationError(error.message || '정답 JSON을 읽을 수 없습니다.');
    }
  };

  const runRagEvaluation = async () => {
    if (!evaluationDataset || evaluationRunningRef.current) return;
    evaluationRunningRef.current = true;
    setEvaluationRunning(true);
    setEvaluationError(''); setEvaluationResult(null);
    setEvaluationStatus('평가 중...');
    try {
      const { data } = await apiClient.post('/rag/evaluate', evaluationDataset, { timeout: 3600000 });
      localStorage.setItem('pic_to_text_rag_evaluation_latest', JSON.stringify(data));
      setEvaluationResult(data); setEvaluationStatus('완료');
    } catch (error) {
      const detail = error.response?.data?.detail;
      setEvaluationError(typeof detail === 'string' ? detail : JSON.stringify(detail || error.message));
      setEvaluationStatus('실패');
    } finally {
      evaluationRunningRef.current = false;
      setEvaluationRunning(false);
    }
  };

  const escapeHtml = (value) => String(value).replace(/[&<>"']/g, (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[character]));
  const scrapbookHtml = () => `<html><head><meta charset="utf-8"><title>내 지식 바구니</title><style>body{font-family:Arial,sans-serif;padding:36px;color:#172033}h1{color:#173f8f}.card{margin:18px 0;padding:18px;border:1px solid #dce3ee;border-radius:10px}.meta{color:#718096;font-size:12px}.answer{white-space:pre-wrap;line-height:1.7}</style></head><body><h1>내 지식 바구니</h1>${scrapbook.map((item) => `<section class="card"><h2>${escapeHtml(item.title)}</h2><p class="meta">${escapeHtml(item.documentName)} · ${new Date(item.createdAt).toLocaleString('ko-KR')}</p><div class="answer">${escapeHtml(item.answer)}</div></section>`).join('')}</body></html>`;
  const exportWord = () => {
    const url = URL.createObjectURL(new Blob(['\ufeff', scrapbookHtml()], { type: 'application/msword;charset=utf-8' }));
    const anchor = document.createElement('a');
    anchor.href = url; anchor.download = '지식-바구니.doc'; anchor.click(); URL.revokeObjectURL(url);
  };
  const exportPdf = () => {
    const popup = window.open('', '_blank');
    if (!popup) return;
    popup.opener = null;
    popup.document.write(scrapbookHtml()); popup.document.close(); popup.focus(); popup.print();
  };

  return <div className="app-shell chat-app-shell"><Sidebar />
    <main className="chat-workspace page-enter">
      <header className="chat-page-header"><div><p>DOCUMENT AI WORKSPACE</p><h1>AI 문서 채팅</h1><span>{modelConfig.model}과 문서 근거를 활용한 AI 작업 공간</span></div><div className="chat-model-status"><i className={modelConfig.ready ? '' : 'offline'} /> {modelConfig.model}</div></header>
      <input ref={fileRef} hidden multiple type="file" accept=".pdf,.png,.jpg,.jpeg,.webp,.bmp,.tif,.tiff,.docx,.xlsx,.xlsm,.txt,.md,.csv" onChange={(e) => { uploadFiles([...e.target.files]).catch(() => setIndexingId(null)); e.target.value = ''; }} />
      {isDeveloper && <input ref={evaluationFileRef} hidden disabled={evaluationRunning} type="file" accept=".json,application/json" onChange={(event) => { loadEvaluationDataset(event.target.files?.[0]); event.target.value = ''; }} />}

      <section className="rag-grid">
        <aside className="history-panel">
          <div className="rag-panel-title"><div><strong>기록 보관함</strong><small>RAG 문서 {documents.length}개 · 대화 {sessions.length}개</small></div></div>
          <section className="history-section rag-document-history"><header><strong>RAG 문서 이력</strong><div className="history-header-actions"><button type="button" disabled={!documents.length || deleting} onClick={() => requestDelete('rag', 'all')}>전체삭제</button><span>{documents.length}</span></div></header><div>{pagedDocuments.map((document) => <div key={document.id} className={`rag-history-row ${activeId === document.id && !uploadMode ? 'active' : ''}`} role="button" tabIndex="0" onClick={() => { setActiveId(document.id); setUploadMode(false); startNewChat(); }} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); setActiveId(document.id); setUploadMode(false); startNewChat(); } }}><span className="history-file-icon">▤</span><div><strong>{document.name}</strong><small>{document.status} · {document.chunkCount} chunks</small></div><button type="button" className="delete-session" disabled={deleting} onClick={(event) => { event.stopPropagation(); requestDelete('rag', 'single', document.id); }} onKeyDown={(event) => event.stopPropagation()} title="RAG 기록 삭제" aria-label={`${document.name} 기록 삭제`}><IoTrashOutline /></button></div>)}{!documents.length && <p>업로드된 RAG 문서가 없습니다.</p>}</div><HistoryPagination page={ragHistoryPage} totalItems={documents.length} onChange={setRagHistoryPage} label="RAG 문서 이력" /></section>
          <section className="history-section chat-history-section"><header><strong>채팅 이력</strong><div className="history-header-actions"><button type="button" disabled={!sessions.length || deleting} onClick={() => requestDelete('chat', 'all')}>전체삭제</button><span>{sessions.length}</span></div></header><div className="history-list-rag">{pagedSessions.map((session) => <div key={session.id} className={`chat-session-row ${activeSessionId === session.id ? 'active' : ''}`}><button onClick={() => openSession(session)}><span className="history-file-icon">◈</span><div><strong>{session.title}</strong><small>{new Date(session.updated_at || session.created_at).toLocaleString('ko-KR')}</small></div></button><button type="button" className="delete-session" disabled={deleting} onClick={(event) => { event.stopPropagation(); requestDelete('chat', 'single', session.id); }} title="대화 삭제" aria-label={`${session.title} 대화 삭제`}><IoTrashOutline /></button></div>)}
          {!sessions.length && <div className="history-empty">AI와 대화를 시작하면<br />기록이 여기에 저장됩니다.</div>}</div><HistoryPagination page={chatHistoryPage} totalItems={sessions.length} onChange={setChatHistoryPage} label="채팅 이력" /></section>
          <div className="index-summary"><span>INDEX</span><strong>{totalChunks}</strong><small>검색 가능한 전체 청크</small></div>
        </aside>

        <section className={`context-panel ${evidenceFlash ? 'evidence-flash' : ''}`}>
          <div className="rag-panel-title"><div><strong>RAG</strong><small>{uploadMode ? '새 RAG 문서를 업로드하세요' : (activeDoc?.name || '새 RAG 문서를 업로드하세요')}</small></div><div className="rag-title-actions"><span className="source-count">{uploadMode ? 0 : sources.length} SOURCES</span>{activeDoc && !uploadMode && <button type="button" className="clear-document-selection" onClick={clearActiveDocument}>선택 해제</button>}<button type="button" onClick={() => { setUploadMode(true); startNewChat(); }}><RiFileUploadLine /> 문서 추가</button></div></div>
          {ragError && <p className="rag-inline-error" role="alert">{ragError}</p>}
          <div className="evidence-workspace">
            <div className="preview-slot">
              <div className="document-view-tabs" role="tablist" aria-label="RAG 문서 보기 방식">
                <button type="button" role="tab" aria-selected={documentViewMode === 'viewer'} className={documentViewMode === 'viewer' ? 'active' : ''} onClick={() => setDocumentViewMode('viewer')}>문서 보기</button>
                <button type="button" role="tab" aria-selected={documentViewMode === 'summary'} className={documentViewMode === 'summary' ? 'active' : ''} onClick={() => setDocumentViewMode('summary')}>AI 문서 요약</button>
              </div>
              <div className="document-view-content">
                {documentViewMode === 'viewer'
                  ? <EvidencePreview source={previewSource} uploading={Boolean(indexingId)} onUpload={(droppedFiles) => { if (Array.isArray(droppedFiles)) uploadFiles(droppedFiles).catch(() => setIndexingId(null)); else fileRef.current?.click(); }} />
                  : <DocumentSummaryPreview document={activeDoc} />}
                {uploadMode && <button type="button" className="rag-first-upload upload-mode-overlay" disabled={Boolean(indexingId)} onClick={() => fileRef.current?.click()} onDragOver={(event) => { event.preventDefault(); event.dataTransfer.dropEffect = 'copy'; }} onDrop={(event) => { event.preventDefault(); uploadFiles([...event.dataTransfer.files]).catch(() => setIndexingId(null)); }}><RiFileUploadLine /><strong>{indexingId ? 'OCR · RAG 처리 중...' : 'RAG 문서를 업로드하세요'}</strong><p>파일을 이곳으로 드래그하거나 클릭해서 선택하세요.</p><small>PDF · DOCX · 이미지 · XLSX · TXT</small></button>}
              </div>
            </div>
            <div className="topk-panel"><header><strong>TOP-K CHUNKS</strong><span>{sources.length}개 검색</span></header><div className="source-list">{sources.length ? sources.map((source, rank) => <article className={`source-card ${selectedSource?.id === source.id ? 'active' : ''}`} key={source.id} onClick={() => setSelectedSource(source)}><div className="source-card-top"><span>TOP {rank + 1} · CHUNK {source.index}</span><b>{Math.round(source.score * 100)}%</b></div><p>{source.content}</p><footer><span>{source.pageNumber}페이지 · {source.source}</span><button onClick={(event) => { event.stopPropagation(); navigator.clipboard?.writeText(source.content); }}>복사</button></footer></article>) : <div className="source-empty"><span>⌕</span><strong>{activeDoc ? '문서 미리보기가 준비되었습니다' : '질문 후 청크가 표시됩니다'}</strong><p>{activeDoc ? '오른쪽에서 질문하면 관련 Top-K 청크와 bbox가 표시됩니다.' : '먼저 왼쪽 영역에 RAG 문서를 업로드해 주세요.'}</p></div>}</div></div>
          </div>
        </section>

        <section className="conversation-panel">
          <div className="rag-panel-title"><div><strong>AI RAG Chat</strong><small>{activeDoc ? activeDoc.name : '새 대화'}</small></div><button type="button" className="new-chat-button" disabled={busy} onClick={startNewChat}>＋ 새 채팅</button></div>
          <div className="messages-rag">{messages.map((message, i) => <div key={i} className={`rag-message ${message.role}`}><span className="avatar">{message.role === 'assistant' ? 'AI' : '나'}</span><div><small>{message.role === 'assistant' ? 'AI Assistant' : 'You'}</small><p>{message.text}</p><div className="message-actions">{message.sourceCount > 0 && <button className="cited" onClick={() => { const messageSources = Array.isArray(message.sources) ? message.sources : []; setSources(messageSources); setSelectedSource(messageSources[0] || null); setEvidenceFlash(false); requestAnimationFrame(() => setEvidenceFlash(true)); setTimeout(() => setEvidenceFlash(false), 900); document.querySelector('.context-panel')?.scrollIntoView({ behavior: 'smooth', block: 'start' }); }}>⌕ 근거 {message.sourceCount}개 확인</button>}{message.role === 'assistant' && i > 0 && <button className="scrap-answer" disabled={scrapSaving} onClick={() => saveToScrapbook(message, i)}><IoBookmarkOutline /> {scrapSaving ? '저장 중...' : '지식 바구니 담기'}</button>}</div>{scrapError && message.role === 'assistant' && <small className="scrap-error">{scrapError}</small>}</div></div>)}{busy && <div className="rag-message assistant"><span className="avatar">AI</span><div><small>AI Assistant</small><p className="typing"><i /><i /><i /></p></div></div>}<div ref={endRef} /></div>
          <div className="chat-composer"><button className="attach-button" onClick={() => fileRef.current?.click()} title="문서 첨부">＋</button><textarea value={query} onChange={(e) => setQuery(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask(); } }} placeholder={documents.length ? '문서에 대해 질문해 보세요...' : '먼저 왼쪽 + 버튼 또는 이곳의 + 버튼으로 문서를 추가하세요'} /><button className="send-button" disabled={!query.trim() || busy} onClick={ask}>↑</button></div>
          <p className="composer-note">AI 답변은 부정확할 수 있습니다. 중요한 정보는 표시된 문서 근거에서 확인하세요.</p>
        </section>
      </section>

      {isDeveloper && <section className="rag-evaluation-panel">
        <header><div><small>DEVELOPER ONLY</small><h2>RAG 성능 평가</h2><p>현재 BGE-M3 · Vector Search · Reranker · gemma2:2b 전체 파이프라인을 평가합니다.</p></div><span className={`evaluation-state ${evaluationStatus === '완료' ? 'complete' : ''}`}>{evaluationStatus}</span></header>
        <div className="evaluation-toolbar"><div><strong>{evaluationDataset ? `정답 데이터 ${evaluationDataset.cases.length}문항 로드 완료` : '정답 데이터가 없습니다.'}</strong><small>{evaluationDataset?.dataset_name || '지정된 JSON 형식의 평가 파일을 선택하세요.'}</small></div><button type="button" disabled={evaluationRunning} onClick={() => evaluationFileRef.current?.click()}>정답 JSON 업로드</button><button type="button" className="run" disabled={!evaluationDataset || evaluationRunning} onClick={runRagEvaluation}>평가 실행</button></div>
        {evaluationError && <p className="evaluation-error">{evaluationError}</p>}
        <div className="evaluation-metrics">{[
          ['Hit@K', 'hit_at_k'], ['Recall@K', 'recall_at_k'], ['MRR', 'mrr'], ['NDCG@K', 'ndcg_at_k'],
          ['Answer Accuracy', 'answer_accuracy'], ['Citation / Source', 'citation_accuracy'], ['Unanswerable Rejection', 'unanswerable_rejection_rate'],
        ].map(([label, key]) => <article key={key}><span>{label}</span><strong>{evaluationResult ? `${(Number(evaluationResult.summary?.[key] || 0) * 100).toFixed(1)}%` : '—'}</strong></article>)}</div>
        {evaluationResult && <footer>총 {evaluationResult.summary.total}문항 · Top-K {evaluationResult.summary.top_k} · 답변 유사도 기준 {(evaluationResult.summary.answer_threshold * 100).toFixed(0)}%</footer>}
      </section>}

      <button className="knowledge-pocket" type="button" title="지식 바구니" aria-label={`지식 바구니, ${scrapbook.length}개`} onClick={() => setScrapbookOpen(true)}><IoBookmarkOutline /><b>{scrapbook.length}</b></button>
      {scrapbookOpen && <div className="scrapbook-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setScrapbookOpen(false); }}><section className="scrapbook-modal" role="dialog" aria-modal="true" aria-label="내 지식 바구니"><header><h2>내 지식 바구니 <span>(Scrapbook)</span></h2><button type="button" onClick={() => setScrapbookOpen(false)} aria-label="닫기"><IoCloseOutline /></button></header><div className="scrapbook-list">{scrapbook.map((item) => <article key={item.id}><div><strong>[AI 답변] {item.title}</strong><button type="button" onClick={() => removeScrap(item.id)}>삭제</button></div><small>{item.documentName} · {new Date(item.createdAt).toLocaleString('ko-KR')} · 근거 {item.sourceCount}개</small><p>{item.answer}</p></article>)}{!scrapbook.length && <div className="scrapbook-empty"><IoBookmarkOutline /><strong>아직 담긴 지식이 없습니다</strong><p>AI 답변 아래의 ‘지식 바구니 담기’를 눌러 보세요.</p></div>}</div><footer><button type="button" className="export-pdf" disabled={!scrapbook.length} onClick={exportPdf}>PDF 보고서 변환</button><button type="button" disabled={!scrapbook.length} onClick={exportWord}>Word 문서 변환</button></footer></section></div>}
      <DeleteConfirmDialog request={deleteRequest} deleting={deleting} error={deleteError} onCancel={closeDeleteConfirm} onConfirm={confirmDelete} />
    </main>
  </div>;
}

export default function ChatPage() {
  return <ChatErrorBoundary><ChatPageContent /></ChatErrorBoundary>;
}
