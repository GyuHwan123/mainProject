import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import Sidebar from '../components/Sidebar';
import { supabase } from '../lib/supabase';
import apiClient from '../api/client';
import { getAppUser, saveAppUser } from '../features/appSession';

const readHistory = () => {
  try { return JSON.parse(localStorage.getItem('pic_to_text_history')) || []; }
  catch { return []; }
};

export default function DashboardPage() {
  const [history, setHistory] = useState(readHistory);
  const [filter, setFilter] = useState('전체');
  const [uploading, setUploading] = useState(false);
  const [notice, setNotice] = useState('');
  const [user, setUser] = useState(getAppUser);
  const cloudInput = useRef(null);
  const navigate = useNavigate();
  const visibleHistory = useMemo(() => filter === '전체' ? history : history.filter((item) => item.type.includes(filter)), [filter, history]);
  const initials = (user.name || user.email || 'U').trim().slice(0, 2).toUpperCase();

  useEffect(() => {
    let active = true;
    apiClient.get('/auth/me').then(({ data }) => {
      if (!active) return;
      setUser(data);
      saveAppUser(data);
    }).catch(() => {});
    return () => { active = false; };
  }, []);

  const cloudUpload = async (file) => {
    if (!file) return;
    if (!supabase) {
      setNotice('Supabase 환경 변수를 설정하면 클라우드 저장을 사용할 수 있습니다.');
      return;
    }
    setUploading(true); setNotice('');
    try {
      const { data: auth } = await supabase.auth.getUser();
      const owner = auth.user?.id || 'anonymous';
      const safeName = file.name.replace(/[^a-zA-Z0-9가-힣._-]/g, '_');
      const path = `${owner}/${Date.now()}-${safeName}`;
      const { error } = await supabase.storage.from('documents').upload(path, file);
      if (error) throw error;
      const next = [{ id: path, name: file.name, type: '클라우드 저장', icon: '☁', date: '방금 전', status: '저장됨', route: '#' }, ...history];
      setHistory(next); localStorage.setItem('pic_to_text_history', JSON.stringify(next));
      setNotice('클라우드에 안전하게 저장했습니다.');
    } catch (error) { setNotice(error.message || '클라우드 저장에 실패했습니다.'); }
    finally { setUploading(false); }
  };

  return <div className="app-shell dashboard-shell"><Sidebar />
    <main className="home-dashboard">
      <header className="home-header"><div><p className="eyebrow">GOOD MORNING</p><h1>문서 작업을 시작해 볼까요?</h1><p>최근 작업을 이어가거나 새로운 문서를 처리하세요.</p></div><div className="home-profile"><span>{initials}</span><div><strong>{user.name || '사용자'} 님</strong><small>{user.email || '로그인 정보를 불러오는 중'}</small></div></div></header>

      <section className="recent-section">
        <div className="section-title-row"><div><h2>최근 히스토리</h2><p>최근에 처리한 문서와 작업 내역입니다.</p></div><div className="history-filters">{['전체', 'OCR', 'AI', '클라우드'].map((item) => <button key={item} className={filter === item ? 'active' : ''} onClick={() => setFilter(item)}>{item}</button>)}</div></div>
        <div className="recent-table"><div className="recent-table-head"><span>문서</span><span>작업</span><span>최근 활동</span><span>상태</span><span /></div>
          {visibleHistory.length ? visibleHistory.slice(0, 5).map((item) => <button className="recent-row" key={item.id} onClick={() => item.route !== '#' && navigate(item.route)}><span className="recent-doc"><i>{item.icon}</i><b>{item.name}</b></span><span>{item.type}</span><span>{item.date}</span><span><em>{item.status}</em></span><span className="row-arrow">›</span></button>) : <div className="no-history">최근에 업로드한 파일이 없습니다</div>}
        </div>
      </section>

      <section className="quick-section"><div className="section-title-row"><div><h2>빠른 시작</h2><p>필요한 기능을 선택하고 바로 시작하세요.</p></div></div>
        <div className="feature-grid">
          <Link to="/ocr" className="feature-card ocr-feature"><div className="feature-icon">▤</div><span className="feature-number">01</span><div><small>PDF TO TEXT</small><h3>OCR 텍스트 추출</h3><p>PDF의 페이지를 미리 보며<br />문자 레이어를 빠르게 추출합니다.</p></div><b className="feature-cta">문서 추출하기 <i>→</i></b><span className="feature-art">Aa</span></Link>
          <Link to="/chat" className="feature-card ai-feature"><div className="feature-icon">✦</div><span className="feature-number">02</span><div><small>ASK YOUR DOCUMENT</small><h3>AI 문서 채팅</h3><p>RAG 검색으로 관련 근거를 찾고<br />문서에 기반한 답변을 받습니다.</p></div><b className="feature-cta">AI에게 질문하기 <i>→</i></b><span className="feature-art">✦</span></Link>
          <button className="feature-card cloud-feature" onClick={() => cloudInput.current?.click()} disabled={uploading}><div className="feature-icon">☁</div><span className="feature-number">03</span><div><small>SECURE STORAGE</small><h3>{uploading ? '업로드 중...' : '클라우드 저장'}</h3><p>중요한 문서를 안전하게 보관하고<br />언제 어디서나 다시 확인합니다.</p></div><b className="feature-cta">파일 업로드 <i>↑</i></b><span className="feature-art">☁</span></button>
          <input ref={cloudInput} hidden type="file" onChange={(e) => cloudUpload(e.target.files?.[0])} />
        </div>
      </section>
      {notice && <button className="dashboard-toast" onClick={() => setNotice('')}>{notice}<span>×</span></button>}
    </main>
  </div>;
}
