import { useMemo, useRef, useState } from 'react';
import * as pdfjsLib from 'pdfjs-dist';
import pdfWorker from 'pdfjs-dist/build/pdf.worker.min.mjs?url';
import { IoChatbubbleEllipsesOutline, IoCloudUploadOutline, IoDocumentsOutline, IoDownloadOutline, IoSchoolOutline, IoTrashOutline } from 'react-icons/io5';
import Sidebar from '../components/Sidebar';
import apiClient from '../api/client';
import '../style/ChatPage.scss';

pdfjsLib.GlobalWorkerOptions.workerSrc = pdfWorker;

const tokenize = (text) => [...new Set(text.toLowerCase().match(/[가-힣a-z0-9]{2,}/g) || [])];
const makeChunks = (text, name) => {
  const clean = text.replace(/\s+/g, ' ').trim();
  const chunks = [];
  for (let start = 0; start < clean.length; start += 700) {
    const content = clean.slice(start, start + 900);
    if (content.trim()) chunks.push({ id: `${name}-${chunks.length + 1}`, content, source: name, index: chunks.length + 1 });
  }
  return chunks;
};
const rankChunks = (query, chunks) => {
  const terms = tokenize(query);
  return chunks.map((chunk) => {
    const lower = chunk.content.toLowerCase();
    const matches = terms.filter((term) => lower.includes(term)).length;
    return { ...chunk, score: terms.length ? matches / terms.length : 0 };
  }).sort((a, b) => b.score - a.score).slice(0, 4);
};

export default function ChatPage() {
  const [view, setView] = useState('chat');
  const [documents, setDocuments] = useState([]);
  const [activeId, setActiveId] = useState(null);
  const [messages, setMessages] = useState([{ role: 'assistant', text: '안녕하세요. 문서를 업로드한 뒤 궁금한 내용을 질문해 주세요. 문서에서 관련 근거를 찾아 답변해 드립니다.' }]);
  const [query, setQuery] = useState('');
  const [sources, setSources] = useState([]);
  const [busy, setBusy] = useState(false);
  const [trainingRows, setTrainingRows] = useState([{ question: '', answer: '' }]);
  const [trainingName, setTrainingName] = useState('docunex-gemma2-dataset');
  const fileRef = useRef(null);
  const endRef = useRef(null);
  const activeDoc = documents.find((item) => item.id === activeId);
  const totalChunks = useMemo(() => documents.reduce((sum, item) => sum + item.chunks.length, 0), [documents]);
  const validTrainingRows = trainingRows.filter((row) => row.question.trim() && row.answer.trim());

  const uploadFiles = async (files) => {
    const loaded = [];
    for (const file of files) {
      let text = '';
      if (file.type === 'application/pdf' || file.name.toLowerCase().endsWith('.pdf')) {
        const pdf = await pdfjsLib.getDocument({ data: await file.arrayBuffer() }).promise;
        const pages = await Promise.all(Array.from({ length: pdf.numPages }, async (_, i) => {
          const content = await (await pdf.getPage(i + 1)).getTextContent();
          return content.items.map((item) => item.str).join(' ');
        }));
        text = pages.join('\n');
      } else text = await file.text();
      const id = `${file.name}-${Date.now()}-${loaded.length}`;
      loaded.push({ id, name: file.name, size: file.size, chunks: makeChunks(text, file.name), createdAt: new Date() });
    }
    if (loaded.length) {
      setDocuments((previous) => [...loaded, ...previous]);
      setActiveId(loaded[0].id);
      setSources([]);
    }
  };

  const ask = async () => {
    const question = query.trim();
    if (!question || busy) return;
    const available = activeDoc?.chunks || documents.flatMap((doc) => doc.chunks);
    const relevant = rankChunks(question, available);
    setMessages((items) => [...items, { role: 'user', text: question }]);
    setQuery(''); setSources(relevant); setBusy(true);
    try {
      const context = relevant.map((chunk) => `[${chunk.source} / Chunk ${chunk.index}] ${chunk.content}`).join('\n\n');
      const { data } = await apiClient.post('/chatbot/ask', { message: question, context }, { timeout: 90000 });
      setMessages((items) => [...items, { role: 'assistant', text: data.reply, sourceCount: relevant.length }]);
    } catch {
      const best = relevant.filter((item) => item.score > 0);
      const fallback = best.length
        ? `문서에서 다음과 같은 관련 내용을 찾았습니다.\n\n${best[0].content}\n\n현재 AI 모델 서버에 연결할 수 없어 가장 관련도 높은 문서 근거를 대신 표시했습니다.`
        : '문서에서 질문과 직접 관련된 내용을 찾지 못했습니다. 질문에 문서에 등장하는 핵심 단어를 포함해 다시 시도해 주세요.';
      setMessages((items) => [...items, { role: 'assistant', text: fallback, sourceCount: best.length }]);
    } finally { setBusy(false); setTimeout(() => endRef.current?.scrollIntoView({ behavior: 'smooth' }), 20); }
  };

  const removeDocument = (id) => {
    setDocuments((items) => items.filter((item) => item.id !== id));
    if (activeId === id) setActiveId(null);
    setSources([]);
  };

  const exportTrainingData = () => {
    if (!validTrainingRows.length) return;
    const jsonl = validTrainingRows.map((row) => JSON.stringify({
      instruction: row.question.trim(), input: '', output: row.answer.trim(),
    })).join('\n');
    const url = URL.createObjectURL(new Blob([jsonl], { type: 'application/jsonl;charset=utf-8' }));
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `${trainingName.trim() || 'fine-tuning-dataset'}.jsonl`;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  return <div className="app-shell chat-app-shell"><Sidebar />
    <main className="chat-workspace">
      <header className="chat-page-header"><div><p>DOCUMENT AI WORKSPACE</p><h1>AI 문서 채팅</h1><span>Gemma2:2b 모델과 문서 근거를 활용한 AI 작업 공간</span></div><div className="chat-model-status"><i /> gemma2:2b</div></header>
      <nav className="chat-view-tabs">
        <button className={view === 'chat' ? 'active' : ''} onClick={() => setView('chat')}><IoChatbubbleEllipsesOutline /> AI 채팅</button>
        <button className={view === 'rag' ? 'active' : ''} onClick={() => setView('rag')}><IoDocumentsOutline /> RAG 문서</button>
        <button className={view === 'training' ? 'active' : ''} onClick={() => setView('training')}><IoSchoolOutline /> Fine-tuning</button>
      </nav>
      <input ref={fileRef} hidden multiple type="file" accept=".pdf,.txt,.md" onChange={(e) => { uploadFiles([...e.target.files]); e.target.value = ''; }} />

      {view === 'chat' && <section className="rag-grid">
        <aside className="history-panel">
          <div className="rag-panel-title"><div><strong>검색 히스토리</strong><small>{documents.length}개의 문서</small></div><button onClick={() => fileRef.current?.click()}>＋</button></div>
          <div className="history-list-rag">{documents.map((doc) => <button key={doc.id} className={`history-doc ${activeId === doc.id ? 'active' : ''}`} onClick={() => { setActiveId(doc.id); setSources([]); }}><span className="history-file-icon">▤</span><div><strong>{doc.name}</strong><small>{doc.chunks.length} chunks · {doc.createdAt.toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' })}</small></div></button>)}
          {!documents.length && <div className="history-empty">업로드한 문서와<br />검색 기록이 여기에 남습니다.</div>}</div>
          <div className="index-summary"><span>INDEX</span><strong>{totalChunks}</strong><small>검색 가능한 전체 청크</small></div>
        </aside>

        <section className="context-panel">
          <div className="rag-panel-title"><div><strong>검색된 문서 근거</strong><small>{activeDoc?.name || '전체 문서'}</small></div><span className="source-count">{sources.length} SOURCES</span></div>
          <div className="source-list">{sources.length ? sources.map((source) => <article className="source-card" key={source.id}><div className="source-card-top"><span>CHUNK {source.index}</span><b>{Math.round(source.score * 100)}% 일치</b></div><p>{source.content}</p><footer><span>▤ {source.source}</span><button onClick={() => navigator.clipboard?.writeText(source.content)}>복사</button></footer></article>) : <div className="source-empty"><span>⌕</span><strong>아직 검색된 근거가 없습니다</strong><p>문서를 업로드하고 질문하면<br />관련 청크가 여기에 표시됩니다.</p></div>}</div>
        </section>

        <section className="conversation-panel">
          <div className="rag-panel-title"><div><strong>AI RAG Chat</strong><small>{activeDoc ? activeDoc.name : '새 대화'}</small></div><button className="more-button">•••</button></div>
          <div className="messages-rag">{messages.map((message, i) => <div key={i} className={`rag-message ${message.role}`}><span className="avatar">{message.role === 'assistant' ? 'AI' : '나'}</span><div><small>{message.role === 'assistant' ? 'AI Assistant' : 'You'}</small><p>{message.text}</p>{message.sourceCount > 0 && <button className="cited" onClick={() => document.querySelector('.context-panel')?.scrollIntoView({ behavior: 'smooth' })}>⌕ 근거 {message.sourceCount}개 확인</button>}</div></div>)}{busy && <div className="rag-message assistant"><span className="avatar">AI</span><div><small>AI Assistant</small><p className="typing"><i /><i /><i /></p></div></div>}<div ref={endRef} /></div>
          <div className="chat-composer"><button className="attach-button" onClick={() => fileRef.current?.click()} title="문서 첨부">＋</button><textarea value={query} onChange={(e) => setQuery(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask(); } }} placeholder={documents.length ? '문서에 대해 질문해 보세요...' : '먼저 왼쪽 + 버튼 또는 이곳의 + 버튼으로 문서를 추가하세요'} /><button className="send-button" disabled={!query.trim() || busy} onClick={ask}>↑</button></div>
          <p className="composer-note">AI 답변은 부정확할 수 있습니다. 중요한 정보는 표시된 문서 근거에서 확인하세요.</p>
        </section>
      </section>}

      {view === 'rag' && <section className="rag-library">
        <div className="rag-library-head"><div><h2>RAG 지식 문서</h2><p>문서를 업로드하면 텍스트를 청크로 나누어 질문 검색에 사용합니다.</p></div><button onClick={() => fileRef.current?.click()}><IoCloudUploadOutline /> 문서 업로드</button></div>
        <div className="rag-summary"><div><span>등록 문서</span><strong>{documents.length}</strong></div><div><span>검색 청크</span><strong>{totalChunks}</strong></div><div><span>지원 형식</span><strong>PDF · TXT · MD</strong></div></div>
        <div className="rag-document-table"><header><span>문서명</span><span>파일 크기</span><span>청크</span><span>관리</span></header>{documents.map((doc) => <div key={doc.id}><strong>{doc.name}</strong><span>{(doc.size / 1024).toFixed(1)} KB</span><span>{doc.chunks.length}</span><button onClick={() => removeDocument(doc.id)} title="삭제"><IoTrashOutline /></button></div>)}{!documents.length && <button className="rag-upload-empty" onClick={() => fileRef.current?.click()}><IoCloudUploadOutline /><strong>RAG 문서를 추가해 주세요</strong><span>PDF, TXT, MD 파일을 사용할 수 있습니다.</span></button>}</div>
      </section>}

      {view === 'training' && <section className="fine-tuning-view">
        <aside className="training-settings"><h2>Fine-tuning 설정</h2><p>Gemma2 학습에 사용할 데이터셋을 준비합니다.</p><label>데이터셋 이름<input value={trainingName} onChange={(e) => setTrainingName(e.target.value)} /></label><label>Base model<input value="gemma2:2b" disabled /></label><div className="training-info"><strong>학습 데이터 준비</strong><p>이 화면에서는 Q&A 데이터 검증과 JSONL 내보내기를 지원합니다. 실제 LoRA 학습 실행에는 별도의 GPU 학습 서버가 필요합니다.</p></div></aside>
        <section className="training-dataset"><header><div><h2>Q&A 학습 데이터</h2><p>질문과 모델이 생성해야 할 이상적인 답변을 입력하세요.</p></div><button onClick={() => setTrainingRows((rows) => [...rows, { question: '', answer: '' }])}>＋ 샘플 추가</button></header><div className="training-list">{trainingRows.map((row, index) => <article key={index}><div><strong>샘플 {index + 1}</strong><button disabled={trainingRows.length === 1} onClick={() => setTrainingRows((rows) => rows.filter((_, rowIndex) => rowIndex !== index))}><IoTrashOutline /></button></div><label>질문 / 지시<textarea value={row.question} onChange={(e) => setTrainingRows((rows) => rows.map((item, rowIndex) => rowIndex === index ? { ...item, question: e.target.value } : item))} placeholder="예: 이 문서의 핵심 내용을 요약해 주세요." /></label><label>이상적인 답변<textarea value={row.answer} onChange={(e) => setTrainingRows((rows) => rows.map((item, rowIndex) => rowIndex === index ? { ...item, answer: e.target.value } : item))} placeholder="모델이 학습할 답변을 입력하세요." /></label></article>)}</div><footer><span><b>{validTrainingRows.length}</b> / {trainingRows.length}개 유효 샘플</span><button disabled={!validTrainingRows.length} onClick={exportTrainingData}><IoDownloadOutline /> JSONL 내보내기</button></footer></section>
      </section>}
    </main>
  </div>;
}
