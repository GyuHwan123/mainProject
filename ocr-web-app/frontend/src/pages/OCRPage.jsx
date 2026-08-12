import { useEffect, useRef, useState } from 'react';
import * as pdfjsLib from 'pdfjs-dist';
import pdfWorker from 'pdfjs-dist/build/pdf.worker.min.mjs?url';
import { IoMdSettings } from 'react-icons/io';
import { IoDocumentTextOutline, IoLogOutOutline } from 'react-icons/io5';
import apiClient from '../api/client';
import { clearAppSession, getAppUser, saveAppUser } from '../features/appSession';
import { supabase } from '../lib/supabase';
import '../style/OCRPage.scss';

pdfjsLib.GlobalWorkerOptions.workerSrc = pdfWorker;

const EMPTY_FILE_NAME = '문서를 선택해 주세요';
const LOCAL_OLLAMA_URL = 'http://127.0.0.1:11434';

function buildTransformPrompt(text, mode) {
  const instruction = mode === 'structured'
    ? `문서의 원래 언어를 유지하며 내용을 구조화하세요.
반드시 다음 JSON 형식만 반환하세요:
{"title":"문서 제목","summary":"핵심 요약","sections":[{"heading":"항목 제목","content":"항목 내용"}]}
원문에 없는 사실은 만들지 말고 sections는 중요한 순서대로 구성하세요.`
    : `문서에서 표로 표현할 수 있는 사실과 관계를 찾아 표로 정리하세요.
반드시 다음 JSON 형식만 반환하세요:
{"title":"표 제목","columns":["열1","열2"],"rows":[["값1","값2"]],"note":"필요한 설명"}
각 행의 값 개수는 columns 개수와 같아야 합니다. 근거가 부족하면 columns와 rows를 빈 배열로 반환하고 원문에 없는 사실은 만들지 마세요.`;
  return `${instruction}\n\n[원문]\n${text.slice(0, 12000)}`;
}

async function transformWithLocalOllama(text, mode) {
  const response = await fetch(`${LOCAL_OLLAMA_URL}/api/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      model: 'gemma2:2b',
      prompt: buildTransformPrompt(text, mode),
      format: 'json',
      stream: false,
      options: { temperature: 0.1 },
    }),
  });
  if (!response.ok) throw new Error(`Ollama 응답 오류 (${response.status})`);
  const payload = await response.json();
  const result = JSON.parse(payload.response || '{}');
  if (!result || typeof result !== 'object' || Array.isArray(result)) throw new Error('Ollama 응답 형식이 올바르지 않습니다.');
  return result;
}

function buildReadingOrder(content, viewport) {
  const words = content.items.filter((item) => item.str?.trim()).map((item) => {
    const transform = pdfjsLib.Util.transform(viewport.transform, item.transform);
    const x = transform[4];
    const baselineY = transform[5];
    const height = Math.max(Math.hypot(transform[2], transform[3]), Math.abs(item.height || 0), 7);
    const width = Math.max(Math.abs(item.width || 0), 2);
    return {
      text: item.str.trim(),
      baselineY,
      height,
      bbox: [
        [Math.max(0, x), Math.max(0, baselineY - height * 1.05)],
        [Math.min(viewport.width, x + width), Math.min(viewport.height, baselineY + height * 0.2)],
      ],
    };
  }).filter((item) => item.bbox.flat().every(Number.isFinite)).sort((a, b) => a.baselineY - b.baselineY);

  const midpoint = viewport.width / 2;
  const centerBand = viewport.width * 0.006;
  const clearLeftItems = words.filter((word) => word.bbox[1][0] < midpoint - centerBand);
  const clearRightItems = words.filter((word) => word.bbox[0][0] > midpoint + centerBand);
  const hasTwoColumns = clearLeftItems.length >= 5 && clearRightItems.length >= 5;

  if (hasTwoColumns) {
    words.forEach((word) => {
      const [x0] = word.bbox[0];
      const [x1] = word.bbox[1];
      if (x0 < midpoint - centerBand && x1 > midpoint + centerBand) word.column = 'spanning';
      else word.column = (x0 + x1) / 2 < midpoint ? 'left' : 'right';
    });
  }

  const lines = [];
  words.forEach((word) => {
    let line = lines.find((candidate) => (
      (!hasTwoColumns || candidate.column === word.column)
      && Math.abs(candidate.baselineY - word.baselineY) <= Math.max(7, Math.max(candidate.height, word.height) * 0.75)
    ));
    if (!line) {
      line = { words: [], baselineY: word.baselineY, height: word.height, column: word.column };
      lines.push(line);
    }
    line.words.push(word);
    line.baselineY = line.words.reduce((sum, value) => sum + value.baselineY, 0) / line.words.length;
    line.height = line.words.reduce((sum, value) => sum + value.height, 0) / line.words.length;
  });

  const splitLines = lines.flatMap((line) => {
    line.words.sort((a, b) => a.bbox[0][0] - b.bbox[0][0]);
    const segments = [];
    let segment = [];
    line.words.forEach((word) => {
      const previous = segment.at(-1);
      const horizontalGap = previous ? word.bbox[0][0] - previous.bbox[1][0] : 0;
      const splitThreshold = Math.max(viewport.width * 0.055, Math.max(line.height, word.height) * 3.5);
      if (previous && horizontalGap > splitThreshold) {
        segments.push(segment);
        segment = [];
      }
      segment.push(word);
    });
    if (segment.length) segments.push(segment);
    return segments;
  });

  const mergedLines = splitLines.map((wordsInLine) => {
    const xs = wordsInLine.flatMap((word) => [word.bbox[0][0], word.bbox[1][0]]);
    const ys = wordsInLine.flatMap((word) => [word.bbox[0][1], word.bbox[1][1]]);
    return { text: wordsInLine.map((word) => word.text).join(' '), bbox: [[Math.min(...xs), Math.min(...ys)], [Math.max(...xs), Math.max(...ys)]] };
  });

  const gutter = viewport.width * 0.04;
  const left = mergedLines.filter((line) => line.bbox[1][0] < midpoint + gutter);
  const right = mergedLines.filter((line) => line.bbox[0][0] > midpoint - gutter);
  if (left.length < 2 || right.length < 2) return mergedLines.sort((a, b) => a.bbox[0][1] - b.bbox[0][1]);

  const columnTop = Math.min(...left.map((line) => line.bbox[0][1]), ...right.map((line) => line.bbox[0][1]));
  const columnBottom = Math.max(...left.map((line) => line.bbox[1][1]), ...right.map((line) => line.bbox[1][1]));
  const spanning = mergedLines.filter((line) => !left.includes(line) && !right.includes(line));
  const byTop = (a, b) => a.bbox[0][1] - b.bbox[0][1];
  return [
    ...spanning.filter((line) => line.bbox[1][1] <= columnTop).sort(byTop),
    ...left.sort(byTop),
    ...right.sort(byTop),
    ...spanning.filter((line) => line.bbox[1][1] > columnTop && line.bbox[0][1] < columnBottom).sort(byTop),
    ...spanning.filter((line) => line.bbox[0][1] >= columnBottom).sort(byTop),
  ];
}

function PdfCanvas({ pdf, pageNumber, scale = 1.25, thumbnail = false, items = [], selectedItemIndex = null, onSelectItem }) {
  const canvasRef = useRef(null);
  const selectedOverlayRef = useRef(null);
  const [canvasSize, setCanvasSize] = useState({ width: 0, height: 0 });

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
      setCanvasSize({ width: viewport.width, height: viewport.height });
      renderTask = page.render({ canvasContext: canvas.getContext('2d'), viewport });
    });
    return () => {
      cancelled = true;
      renderTask?.cancel();
    };
  }, [pdf, pageNumber, scale]);

  useEffect(() => {
    selectedOverlayRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'center' });
  }, [selectedItemIndex]);

  if (thumbnail) return <canvas ref={canvasRef} className="pdf-thumb-canvas" />;
  return (
    <div className="pdf-preview-wrap" style={canvasSize}>
      <canvas ref={canvasRef} className="pdf-main-canvas" />
      {items.map((item, index) => {
        const [[x0, y0], [x1, y1]] = item.bbox;
        return <button ref={selectedItemIndex === index ? selectedOverlayRef : null} key={`${index}-${item.text}`} type="button" className={`bbox-overlay ${selectedItemIndex === index ? 'selected' : ''}`} style={{ left: x0 * scale, top: y0 * scale, width: Math.max((x1 - x0) * scale, 2), height: Math.max((y1 - y0) * scale, 2) }} onClick={() => onSelectItem?.(index)} aria-label={`${item.text} 위치`} />;
      })}
    </div>
  );
}


export default function OCRPage() {
  const [user, setUser] = useState(getAppUser);
  const [pdf, setPdf] = useState(null);
  const [imagePreviewUrl, setImagePreviewUrl] = useState('');
  const [fileName, setFileName] = useState(EMPTY_FILE_NAME);
  const [pageNumber, setPageNumber] = useState(1);
  const [pageTexts, setPageTexts] = useState([]);
  const [pageItems, setPageItems] = useState([]);
  const [selectedItemIndex, setSelectedItemIndex] = useState(null);
  const [documentHistory, setDocumentHistory] = useState([]);
  const [currentDocumentId, setCurrentDocumentId] = useState(null);
  const [pendingFile, setPendingFile] = useState(null);
  const [zoom, setZoom] = useState(1.05);
  const [loading, setLoading] = useState(false);
  const [projectTransition, setProjectTransition] = useState(false);
  const [resultTab, setResultTab] = useState('text');
  const [aiResults, setAiResults] = useState({});
  const [aiLoading, setAiLoading] = useState(false);
  const [aiError, setAiError] = useState('');
  const [groundTruth, setGroundTruth] = useState('');
  const [processingTimeMs, setProcessingTimeMs] = useState(null);
  const [evaluationStatus, setEvaluationStatus] = useState('');
  const [error, setError] = useState('');
  const [profileImageFailed, setProfileImageFailed] = useState(false);
  const [showUserMenu, setShowUserMenu] = useState(false);
  const inputRef = useRef(null);
  const imagePreviewRef = useRef('');
  const userMenuRef = useRef(null);
  const projectTransitionTimerRef = useRef(null);
  const extractionStartedAtRef = useRef(null);
  const evaluationPanelRef = useRef(null);
  const groundTruthFileRef = useRef(null);

  const replaceImagePreview = (file) => {
    if (imagePreviewRef.current) URL.revokeObjectURL(imagePreviewRef.current);
    const nextUrl = file ? URL.createObjectURL(file) : '';
    imagePreviewRef.current = nextUrl;
    setImagePreviewUrl(nextUrl);
  };

  const refreshHistory = () => apiClient.get('/ocr/history')
    .then(({ data }) => setDocumentHistory(data))
    .catch(() => {});

  const resetDocumentView = () => {
    setPdf(null);
    replaceImagePreview(null);
    setPageTexts([]);
    setPageItems([]);
    setPageNumber(1);
    setSelectedItemIndex(null);
    setCurrentDocumentId(null);
    setResultTab('text');
    setAiResults({});
    setAiLoading(false);
    setAiError('');
    setGroundTruth('');
    setProcessingTimeMs(null);
    setEvaluationStatus('');
  };

  const startNewProject = () => {
    if (loading || projectTransition) return;
    setProjectTransition(true);
    projectTransitionTimerRef.current = window.setTimeout(() => {
      resetDocumentView();
      setFileName(EMPTY_FILE_NAME);
      setZoom(1.05);
      setError('');
      setPendingFile(null);
      if (inputRef.current) inputRef.current.value = '';
      setProjectTransition(false);
      projectTransitionTimerRef.current = null;
    }, 420);
  };

  const loadHistoryDocument = async (documentId) => {
    setPendingFile(null);
    setLoading(true);
    setError('');
    resetDocumentView();
    try {
      const [{ data: result }, fileResult] = await Promise.all([
        apiClient.get(`/ocr/documents/${documentId}`),
        apiClient.get(`/ocr/documents/${documentId}/file`, { responseType: 'blob', timeout: 60000 }),
      ]);
      const blob = fileResult.data;
      const pages = result.pages || [];

      setPageTexts(pages.map((page) => page.text || ''));
      setPageItems(pages.map((page) => page.items || []));
      setFileName(result.filename);
      setCurrentDocumentId(documentId);

      if (/\.pdf$/i.test(result.filename)) {
        const pdfData = await blob.arrayBuffer();
        setPdf(await pdfjsLib.getDocument({ data: pdfData }).promise);
      } else if (/\.(png|jpe?g|webp|bmp)$/i.test(result.filename)) {
        replaceImagePreview(blob);
      }
    } catch (requestError) {
      setError(requestError.response?.data?.detail || requestError.message || '저장된 문서를 불러오지 못했습니다.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    let active = true;

    apiClient.get('/auth/me')
      .then(({ data }) => {
        if (!active) return;
        setUser(data);
        saveAppUser(data);
      })
      .catch(() => {
        // 저장된 세션 정보로 사용자 영역을 유지합니다.
      });
    refreshHistory();

    return () => {
      active = false;
      if (projectTransitionTimerRef.current) window.clearTimeout(projectTransitionTimerRef.current);
      if (imagePreviewRef.current) URL.revokeObjectURL(imagePreviewRef.current);
    };
  }, []);

  useEffect(() => {
    if (!showUserMenu) return undefined;
    const closeMenu = (event) => {
      if (event.key === 'Escape' || !userMenuRef.current?.contains(event.target)) setShowUserMenu(false);
    };
    document.addEventListener('mousedown', closeMenu);
    document.addEventListener('keydown', closeMenu);
    return () => {
      document.removeEventListener('mousedown', closeMenu);
      document.removeEventListener('keydown', closeMenu);
    };
  }, [showUserMenu]);

  useEffect(() => {
    setResultTab('text');
    setAiError('');
  }, [pageNumber]);

  const logout = async () => {
    setShowUserMenu(false);
    try {
      await supabase?.auth.signOut();
    } finally {
      clearAppSession();
      window.location.replace('/login');
    }
  };

  const loadWithOcr = async (file) => {
    const formData = new FormData();
    formData.append('file', file);
    const { data } = await apiClient.post('/ocr/upload', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 300000,
    });

    setPdf(null);
    setPageTexts((data.pages || []).map((page) => page.text || ''));
    setPageItems((data.pages || []).map((page) => page.items || []));
    setSelectedItemIndex(null);
    setPageNumber(1);
    setFileName(data.filename || file.name);
    setCurrentDocumentId(data.document_id || null);
    refreshHistory();
  };

  const loadPdf = async (file) => {
    if (!file || (file.type !== 'application/pdf' && !/\.pdf$/i.test(file.name))) {
      setError('PDF 파일만 업로드할 수 있습니다.');
      return;
    }
    setLoading(true);
    setError('');
    resetDocumentView();
    setFileName(file.name);
    try {
      const data = await file.arrayBuffer();
      const document = await pdfjsLib.getDocument({ data }).promise;
      const extractedPages = await Promise.all(
        Array.from({ length: document.numPages }, async (_, index) => {
          const page = await document.getPage(index + 1);
          const content = await page.getTextContent();
          const items = buildReadingOrder(content, page.getViewport({ scale: 1 }));
          return { items, text: items.map((item) => item.text).join('\n') };
        }),
      );
      const texts = extractedPages.map((page) => page.text);
      if (texts.some((text) => text.length > 0)) {
        const archiveData = new FormData();
        archiveData.append('file', file);
        archiveData.append('result_json', JSON.stringify({
          filename: file.name,
          content_type: 'text_only',
          pages: extractedPages.map((page, index) => ({
            page: index + 1,
            text: page.text,
            items: page.items.map((item) => ({ text: item.text, confidence: 1, bbox: item.bbox })),
          })),
        }));
        const { data: archived } = await apiClient.post('/ocr/archive', archiveData, {
          headers: { 'Content-Type': 'multipart/form-data' },
          timeout: 60000,
        });
        refreshHistory();
        setPdf(document);
        setPageTexts(texts);
        setPageItems(extractedPages.map((page) => page.items));
        setSelectedItemIndex(null);
        setPageNumber(1);
        setFileName(file.name);
        setCurrentDocumentId(archived.document_id || null);
      } else {
        await loadWithOcr(file);
        setPdf(document);
      }
    } catch (requestError) {
      setError(requestError.response?.data?.detail || 'PDF를 읽지 못했습니다. 손상되었거나 지원하지 않는 파일일 수 있습니다.');
    } finally {
      if (extractionStartedAtRef.current) setProcessingTimeMs(performance.now() - extractionStartedAtRef.current);
      setLoading(false);
    }
  };

  const loadFile = async (file) => {
    if (!file) return;
    extractionStartedAtRef.current = performance.now();

    if (file.type === 'application/pdf' || /\.pdf$/i.test(file.name)) {
      await loadPdf(file);
      return;
    }

    resetDocumentView();
    setFileName(file.name);
    const canPreviewImage = /\.(png|jpe?g|webp|bmp)$/i.test(file.name);
    replaceImagePreview(canPreviewImage ? file : null);

    setLoading(true);
    setError('');
    try {
      await loadWithOcr(file);
    } catch (requestError) {
      setError(requestError.response?.data?.detail || '파일에서 텍스트를 추출하지 못했습니다. OCR 서버 상태와 파일 형식을 확인해 주세요.');
    } finally {
      setProcessingTimeMs(performance.now() - extractionStartedAtRef.current);
      setLoading(false);
    }
  };

  const prepareFile = async (file) => {
    if (!file) return;
    resetDocumentView();
    setPendingFile(file);
    setFileName(file.name);
    setError('');
    try {
      if (file.type === 'application/pdf' || /\.pdf$/i.test(file.name)) {
        const document = await pdfjsLib.getDocument({ data: await file.arrayBuffer() }).promise;
        setPdf(document);
      } else {
        replaceImagePreview(/\.(png|jpe?g|webp|bmp)$/i.test(file.name) ? file : null);
      }
    } catch (previewError) {
      setError(previewError.message || '파일 미리보기를 준비하지 못했습니다.');
    }
  };

  const runExtraction = async () => {
    if (!pendingFile || loading) return;
    await loadFile(pendingFile);
    setPendingFile(null);
  };

  const saveDeveloperEvaluation = async () => {
    if (!currentDocumentId || !groundTruth.trim()) return;
    setEvaluationStatus('저장 중...');
    try {
      await apiClient.post('/reports/evaluations', {
        document_id: currentDocumentId,
        document_name: fileName,
        extracted_text: pageTexts.join('\n\n'),
        ground_truth: groundTruth,
        processing_time_ms: processingTimeMs,
      });
      setEvaluationStatus('평가 데이터가 저장되었습니다. 리포트에서 확인할 수 있습니다.');
    } catch (requestError) {
      setEvaluationStatus(requestError.response?.data?.detail || '평가 데이터를 저장하지 못했습니다.');
    }
  };

  const loadGroundTruthFile = async (file) => {
    if (!file) return;
    try {
      const raw = await file.text();
      if (/\.json$/i.test(file.name) || file.type === 'application/json') {
        const data = JSON.parse(raw);
        const preferred = data?.ground_truth ?? data?.text ?? data?.content ?? data?.answer;
        const value = typeof preferred === 'string'
          ? preferred
          : Array.isArray(preferred)
            ? preferred.map((item) => typeof item === 'string' ? item : JSON.stringify(item)).join('\n')
            : typeof data === 'string'
              ? data
              : JSON.stringify(data, null, 2);
        setGroundTruth(value);
      } else {
        setGroundTruth(raw);
      }
      setEvaluationStatus(`${file.name} 파일을 불러왔습니다.`);
    } catch (fileError) {
      setEvaluationStatus(fileError instanceof SyntaxError ? 'JSON 파일 형식이 올바르지 않습니다.' : '정답 데이터 파일을 읽지 못했습니다.');
    }
  };

  const selectResultTab = async (tab, force = false) => {
    setResultTab(tab);
    setAiError('');
    if (tab === 'text' || !currentText.trim()) return;

    const cacheKey = `${currentDocumentId || fileName}:${pageNumber}:${tab}`;
    if (aiResults[cacheKey] && !force) return;

    setAiLoading(true);
    try {
      let result;
      try {
        const { data: status } = await apiClient.get('/chatbot/status', { timeout: 6000 });
        if (!status.ready) {
          result = await transformWithLocalOllama(currentText, tab);
        } else {
          const { data } = await apiClient.post('/chatbot/transform', {
            text: currentText.slice(0, 12000),
            mode: tab,
          }, { timeout: 130000 });
          result = data.result;
        }
      } catch (backendError) {
        if (backendError.response && backendError.response.status !== 503) throw backendError;
        result = await transformWithLocalOllama(currentText, tab);
      }
      setAiResults((results) => ({ ...results, [cacheKey]: result }));
    } catch (requestError) {
      setAiError(requestError.response?.data?.detail || 'AI 결과를 생성하지 못했습니다. Ollama와 gemma2:2b 상태를 확인해 주세요.');
    } finally {
      setAiLoading(false);
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
  const currentItems = pageItems[pageNumber - 1] || [];
  const aiResultKey = `${currentDocumentId || fileName}:${pageNumber}:${resultTab}`;
  const currentAiResult = aiResults[aiResultKey];
  const pageCount = pdf?.numPages || pageTexts.length;
  const hasResult = pageTexts.length > 0;
  const isDeveloper = ['DEVELOPER', 'ADMIN'].includes(user?.role) || user?.email === 'developer@docunex.com';
  const fileExtension = fileName.includes('.') ? fileName.split('.').pop().toUpperCase() : 'FILE';
  
  return (
    <div className="ocr-app-shell">
      <aside className="sidebar-panel">
        <div className="sidebar-brand">
          <img src="/DocAI.png" alt="DOCUNEX AI" />
        </div>
        <button className="new-project-button" onClick={startNewProject} disabled={loading || projectTransition}>
          {projectTransition ? '전환 중...' : '＋ 새 프로젝트'}
        </button>
        <label className="sidebar-search">
          <span aria-hidden="true">⌕</span>
          <input type="search" placeholder="문서 검색..." />
        </label>
        <div className="sidebar-history">
          <h2>최근 문서</h2>
          {documentHistory.map((document) => (
            <button className={`history-item ${document.id === currentDocumentId ? 'active' : ''}`} type="button" key={document.id} onClick={() => loadHistoryDocument(document.id)} disabled={loading}>
              <span className="doc-icon" aria-hidden="true"><IoDocumentTextOutline /></span>
              <span className="doc-info">
                <strong>{document.file_name}</strong>
                <small>{new Date(document.created_at).toLocaleString('ko-KR')}</small>
              </span>
            </button>
          ))}
          {!documentHistory.length && <div className="empty-pages">저장된 문서가 없습니다.</div>}
        </div>
        <div className="sidebar-user" ref={userMenuRef}>
            {user?.profileImg && !profileImageFailed ? (
                <img 
                src={user.profileImg} 
                alt={`${user.name}의 프로필`} 
                className="user-avatar"
                onError={() => setProfileImageFailed(true)}
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

            <button type="button" aria-label="설정" aria-expanded={showUserMenu} onClick={() => setShowUserMenu((open) => !open)}><IoMdSettings /></button>
            {showUserMenu && (
              <div className="sidebar-user-menu" role="menu">
                <button type="button" role="menuitem" onClick={logout}><IoLogOutOutline /><span>로그아웃</span></button>
              </div>
            )}
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
            {isDeveloper && <button className="developer-jump-button" type="button" onClick={() => evaluationPanelRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })}>비교 텍스트 보기</button>}
            {isDeveloper && <button className="developer-report-button" type="button" onClick={() => { window.location.href = '/reports'; }}>성능 리포트</button>}
            <button className="ocr-primary" onClick={() => inputRef.current?.click()}>파일 선택</button>
          </div>
        </header>

        <input ref={inputRef} hidden type="file" accept=".pdf,.png,.jpg,.jpeg,.webp,.bmp,.tif,.tiff,.docx,.txt,.md,.csv" onChange={(e) => { const file = e.target.files?.[0]; e.target.value = ''; prepareFile(file); }} />

        <div className="ocr-filebar">
          <div className="file-identity">
            <span className="pdf-badge">{fileExtension}</span>
            <span><strong>{fileName}</strong><small>{hasResult ? `${pageCount}페이지 · 텍스트 추출 완료` : pendingFile ? '파일 준비 완료 · 추출 버튼을 눌러주세요' : 'PDF, 이미지, DOCX 및 텍스트 파일'}</small></span>
          </div>
          <div className="filebar-actions">{pendingFile && <button className="extract-start-button" onClick={runExtraction} disabled={loading}>{loading ? '추출 중...' : 'OCR 텍스트 추출'}</button>}{(pendingFile || hasResult) && <button className="ghost-button" onClick={() => inputRef.current?.click()}>파일 변경</button>}</div>
        </div>

        <section className="ocr-editor" onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); prepareFile(e.dataTransfer.files?.[0]); }}>
          <aside className="pages-panel">
            <div className="panel-heading"><span>페이지</span><b>{pageCount}</b></div>
            <div className="thumb-list">
              {hasResult ? Array.from({ length: pageCount }, (_, index) => (
                <button key={index} className={`page-thumb ${pageNumber === index + 1 ? 'active' : ''}`} onClick={() => { setPageNumber(index + 1); setSelectedItemIndex(null); }}>
                  <span className="thumb-paper">{pdf ? <PdfCanvas pdf={pdf} pageNumber={index + 1} scale={0.22} thumbnail /> : imagePreviewUrl ? <img className="image-thumb" src={imagePreviewUrl} alt="업로드 이미지 미리보기" /> : <IoDocumentTextOutline />}</span>
                  <span>{index + 1} 페이지</span>
                </button>
              )) : <div className="empty-pages">PDF를 업로드하면<br />페이지별로 표시됩니다.</div>}

            </div>
          </aside>

          <div className="preview-panel">
            <div className="preview-toolbar">
              <div>
                <button disabled={!hasResult || pageNumber === 1} onClick={() => setPageNumber((p) => p - 1)} aria-label="이전 페이지">‹</button>
                <span>{hasResult ? `${pageNumber} / ${pageCount}` : '0 / 0'}</span>
                <button disabled={!hasResult || pageNumber === pageCount} onClick={() => setPageNumber((p) => p + 1)} aria-label="다음 페이지">›</button>
              </div>
              <strong>문서 미리보기</strong>
              <div>
                <button onClick={() => setZoom((z) => Math.max(0.55, z - 0.15))} aria-label="축소">−</button>
                <span>{Math.round(zoom * 100)}%</span>
                <button onClick={() => setZoom((z) => Math.min(2, z + 0.15))} aria-label="확대">＋</button>
              </div>
            </div>
            <div className="preview-stage">
              {projectTransition ? <div className="loader"><span />새 프로젝트를 준비하고 있습니다...</div> : loading && !imagePreviewUrl ? <div className="loader"><span />파일을 분석하고 있습니다...</div> : pdf ? <PdfCanvas pdf={pdf} pageNumber={pageNumber} scale={zoom} items={currentItems} selectedItemIndex={selectedItemIndex} onSelectItem={setSelectedItemIndex} /> : imagePreviewUrl ? (
                <div className="image-preview-wrap" style={{ width: `${zoom * 100}%` }}>
                  <img className="image-main-preview" src={imagePreviewUrl} alt={`${fileName} 미리보기`} />
                  {loading && <div className="image-processing"><span />OCR 처리 중...</div>}
                </div>
              ) : hasResult ? (
                <div className="loader">OCR 텍스트 추출이 완료되었습니다.</div>
              ) : (
                <button className="dropzone" onClick={() => inputRef.current?.click()}>
                  <span className="drop-icon">⇧</span><strong>PDF를 여기에 놓아주세요</strong><small>또는 클릭해서 파일을 선택하세요</small>
                </button>
              )}
              {error && <div className="ocr-error">{error}</div>}
            </div>
          </div>

          <aside className="text-panel">
            <div className="text-tabs">
              <button className={resultTab === 'text' ? 'active' : ''} onClick={() => selectResultTab('text')}>텍스트 보기</button>
              <button className={resultTab === 'structured' ? 'active' : ''} onClick={() => selectResultTab('structured')} disabled={!hasResult || aiLoading}>구조화</button>
              <button className={resultTab === 'table' ? 'active' : ''} onClick={() => selectResultTab('table')} disabled={!hasResult || aiLoading}>표</button>
              {isDeveloper && <button className={resultTab === 'evaluation' ? 'active' : ''} onClick={() => setResultTab('evaluation')} disabled={!hasResult}>정답 데이터</button>}
            </div>
            <div className="text-header">
              <div><span>{resultTab === 'text' ? '추출된 텍스트' : resultTab === 'structured' ? 'AI 구조화 결과' : resultTab === 'table' ? 'AI 표 변환 결과' : 'OCR 정답 데이터 입력'}</span><small>{hasResult ? `${pageNumber} 페이지` : '대기 중'}</small></div>
              {resultTab === 'text' && <button disabled={!hasResult} onClick={downloadText} title="텍스트 다운로드">⇩</button>}
            </div>
            <div className="text-meta"><span>{currentText.length.toLocaleString()}자</span><span>{resultTab === 'text' ? '텍스트 레이어' : resultTab === 'evaluation' ? 'Ground Truth' : 'Gemma2:2b'}</span></div>
            {resultTab === 'text' ? (
              <div className={`extracted-copy ${!hasResult ? 'placeholder' : ''}`}>
                {hasResult ? (currentItems.length ? currentItems.map((item, index) => (
                  <button key={`${index}-${item.text}`} type="button" className={`extracted-line ${selectedItemIndex === index ? 'selected' : ''}`} onClick={() => setSelectedItemIndex(index)}>{item.text}</button>
                )) : (currentText || '이 페이지에는 추출 가능한 텍스트가 없습니다.')) : '파일을 업로드하면 페이지별 추출 텍스트가 여기에 표시됩니다.'}
              </div>
            ) : resultTab === 'evaluation' ? (
              <div className="developer-ground-truth">
                <div className="evaluation-summary"><span>개발자 전용</span><b>{processingTimeMs ? `${(processingTimeMs / 1000).toFixed(2)}초` : '시간 측정 대기'}</b></div>
                <p>현재 문서의 사람이 검수한 전체 정답 텍스트를 입력하세요. OCR 결과와 비교한 지표는 리포트 페이지에 저장됩니다.</p>
                <textarea value={groundTruth} onChange={(event) => { setGroundTruth(event.target.value); setEvaluationStatus(''); }} placeholder="Ground Truth 전체 텍스트를 입력하세요." />
                <button disabled={!groundTruth.trim() || !currentDocumentId || evaluationStatus === '저장 중...'} onClick={saveDeveloperEvaluation}>정답 데이터 저장 및 평가</button>
                {evaluationStatus && <small className={evaluationStatus.includes('저장되었습니다') ? 'success' : ''}>{evaluationStatus}</small>}
              </div>
            ) : (
              <div className={`ai-result ${aiLoading || aiError || !currentAiResult ? 'placeholder' : ''}`}>
                {aiLoading ? <div className="ai-result-loading"><span />Gemma2가 문서를 분석하고 있습니다...</div> : aiError ? (
                  <div className="ai-result-error"><p>{aiError}</p><button onClick={() => selectResultTab(resultTab, true)}>다시 시도</button></div>
                ) : resultTab === 'structured' && currentAiResult ? (
                  <article className="structured-result">
                    <h3>{currentAiResult.title || '구조화 결과'}</h3>
                    {currentAiResult.summary && <p className="result-summary">{currentAiResult.summary}</p>}
                    {Array.isArray(currentAiResult.sections) && currentAiResult.sections.map((section, index) => (
                      <section key={`${section.heading}-${index}`}><h4>{section.heading || `항목 ${index + 1}`}</h4><p>{section.content}</p></section>
                    ))}
                  </article>
                ) : resultTab === 'table' && currentAiResult ? (
                  <div className="table-result">
                    <h3>{currentAiResult.title || '표 변환 결과'}</h3>
                    {Array.isArray(currentAiResult.columns) && currentAiResult.columns.length && Array.isArray(currentAiResult.rows) && currentAiResult.rows.length ? (
                      <div className="result-table-scroll"><table><thead><tr>{currentAiResult.columns.map((column, index) => <th key={`${column}-${index}`}>{column}</th>)}</tr></thead><tbody>{currentAiResult.rows.map((row, rowIndex) => <tr key={rowIndex}>{currentAiResult.columns.map((_, columnIndex) => <td key={columnIndex}>{row?.[columnIndex] ?? ''}</td>)}</tr>)}</tbody></table></div>
                    ) : <p>현재 페이지에서 표로 구성할 수 있는 내용을 찾지 못했습니다.</p>}
                    {currentAiResult.note && <p className="table-note">{currentAiResult.note}</p>}
                  </div>
                ) : <p>결과를 준비하고 있습니다.</p>}
              </div>
            )}
            <div className="text-note"><b>i</b><p>{resultTab === 'text' ? '일반 PDF는 문자 레이어를 직접 추출하고, 스캔 PDF와 이미지 문서는 PaddleOCR로 인식합니다.' : '추출된 현재 페이지의 텍스트를 로컬 Gemma2:2b 모델로 변환합니다.'}</p></div>
          </aside>
        </section>
        {isDeveloper && <section ref={evaluationPanelRef} className={`developer-evaluation-panel ${hasResult ? '' : 'waiting'}`}>
          <header>
            <div><span>DEVELOPER ONLY</span><h2>OCR 정답 데이터 평가</h2><p>OCR 결과와 사람이 검수한 정답을 비교해 Precision, Recall, F1 Score를 계산합니다.</p></div>
            <div className="developer-evaluation-meta"><small>추출 처리 시간</small><strong>{processingTimeMs ? `${(processingTimeMs / 1000).toFixed(2)}초` : '측정 대기'}</strong></div>
          </header>
          <div className="developer-evaluation-body">
            <div className="developer-extracted-summary"><div><strong>OCR 추출 결과</strong><span>{pageTexts.join('\n\n').length.toLocaleString()}자 · {pageCount}페이지</span></div><pre>{hasResult ? pageTexts.join('\n\n').slice(0, 1800) : '문서를 업로드하고 OCR 추출을 완료하면 결과가 표시됩니다.'}</pre></div>
            <label><div><strong>정답 데이터 (Ground Truth)</strong><span className="ground-truth-tools"><em>TXT · JSON</em><button type="button" disabled={!hasResult} onClick={() => groundTruthFileRef.current?.click()}>파일 불러오기</button></span></div><textarea disabled={!hasResult} value={groundTruth} onChange={(event) => { setGroundTruth(event.target.value); setEvaluationStatus(''); }} placeholder="직접 입력하거나 TXT 또는 JSON 정답 파일을 불러오세요." /><input ref={groundTruthFileRef} hidden type="file" accept=".txt,.json,text/plain,application/json" onChange={(event) => { loadGroundTruthFile(event.target.files?.[0]); event.target.value = ''; }} /></label>
          </div>
          <footer><span className={evaluationStatus.includes('저장되었습니다') ? 'success' : ''}>{evaluationStatus || (hasResult ? '정답 데이터를 입력하면 평가 결과가 개발자 리포트에 저장됩니다.' : '먼저 문서를 업로드해 주세요.')}</span><div><button className="open-report-button" type="button" onClick={() => { window.location.href = '/reports'; }}>리포트 열기</button><button disabled={!hasResult || !groundTruth.trim() || !currentDocumentId || evaluationStatus === '저장 중...'} onClick={saveDeveloperEvaluation}>정답 저장 및 성능 평가</button></div></footer>
        </section>}
      </main>
    </div>
  );
}
