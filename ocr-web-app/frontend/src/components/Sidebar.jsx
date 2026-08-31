import { useEffect, useRef, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';

import { IoLogOutOutline, IoPersonOutline, IoSettingsOutline } from 'react-icons/io5';
import { RiUser3Fill } from "react-icons/ri";
import { MdHomeFilled, MdOutlineChat } from "react-icons/md";
import { CiReceipt } from "react-icons/ci";
import { TbReportAnalytics } from "react-icons/tb";
import { clearAppSession } from '../features/appSession';
import { getAppUser } from '../features/appSession';
import { supabase } from '../lib/supabase';
import '../style/Sidebar.scss';


const items = [
  { label: '대시보드', icon: <MdHomeFilled />, path: '/dashboard' },
  { label: '영수증 자동 문서화', icon: <CiReceipt />, path: '/ocr' },
  { label: 'AI 문서 채팅', icon: <MdOutlineChat />, path: '/chat' },
  { label: '리포트', icon: <TbReportAnalytics />, path: '/reports' },
  { label: '내 정보', icon: <RiUser3Fill />, path: '/mypage' },
];

export default function Sidebar() {
  const location = useLocation();
  const navigate = useNavigate();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [loggingOut, setLoggingOut] = useState(false);
  const settingsRef = useRef(null);
  const currentUser = getAppUser();
  const isDeveloper = ['DEVELOPER', 'ADMIN'].includes(currentUser.role) || currentUser.email === 'developer@docunex.com';
  const canViewReports = currentUser.subscriptionTier === 'ENTERPRISE' || ['DEVELOPER', 'ADMIN'].includes(currentUser.role);
  const visibleItems = items.filter((item) => item.path !== '/reports' || canViewReports);

  useEffect(() => {
    if (!settingsOpen) return undefined;
    const closeSettings = (event) => {
      if (event.key === 'Escape' || !settingsRef.current?.contains(event.target)) setSettingsOpen(false);
    };
    document.addEventListener('mousedown', closeSettings);
    document.addEventListener('keydown', closeSettings);
    return () => {
      document.removeEventListener('mousedown', closeSettings);
      document.removeEventListener('keydown', closeSettings);
    };
  }, [settingsOpen]);

  const logout = async () => {
    if (loggingOut) return;
    setLoggingOut(true);
    try {
      await supabase?.auth.signOut();
    } finally {
      clearAppSession();
      navigate('/login', { replace: true });
      window.location.reload();
    }
  };

  return <aside className="sidebar">
    <Link to="/dashboard" className="brand-wrap" aria-label="대시보드로 이동"><picture className="brand-logo"><source media="(max-width: 1120px)" srcSet="/DocAI.png" /><img src="/DocAI_p-02.png" alt="DocAI" /></picture><div className="brand-name">DocAI</div></Link>
    <nav className="sidebar-nav">{visibleItems.map((item) => {
      const opensReceiptReport = item.path === '/reports' && location.pathname === '/ocr' && isDeveloper;
      const destination = opensReceiptReport ? '/reports?view=developer&developerReport=receipt&receiptTab=experiment' : item.path;
      return <Link key={item.path} to={destination} onClick={() => { if (opensReceiptReport) { localStorage.setItem('pic_to_text_developer_report', 'receipt'); localStorage.setItem('pic_to_text_receipt_report_tab', 'experiment'); } }} className={`nav-item ${location.pathname === item.path ? 'active' : ''}`} title={item.label}><span>{item.icon}</span></Link>;
    })}</nav>
    <div className="sidebar-settings" ref={settingsRef}>
      {settingsOpen && <div className="settings-menu" role="menu">
        <Link to="/mypage" role="menuitem" onClick={() => setSettingsOpen(false)}><IoPersonOutline /><span><strong>마이페이지</strong><small>내 정보 및 계정 관리</small></span></Link>
        <button type="button" role="menuitem" onClick={logout} disabled={loggingOut}><IoLogOutOutline /><span><strong>{loggingOut ? '로그아웃 중...' : '로그아웃'}</strong><small>현재 계정에서 나가기</small></span></button>
      </div>}
      <button className={`sidebar-footer ${settingsOpen ? 'active' : ''}`} type="button" aria-label="설정" aria-expanded={settingsOpen} onClick={() => setSettingsOpen((open) => !open)}>
        <IoSettingsOutline />
        <span className="settings-tooltip" role="tooltip">설정</span>
      </button>
    </div>
  </aside>;
}
