import { Link, useLocation } from 'react-router-dom';

const items = [
  { label: '대시보드', icon: '⌂', path: '/dashboard' },
  { label: 'PDF 추출', icon: '▤', path: '/ocr' },
  { label: 'AI 채팅', icon: '✦', path: '/chat' },
  { label: '리포트', icon: '▥', path: '/reports' },
  { label: '내 정보', icon: '○', path: '/mypage' },
];

export default function Sidebar() {
  const location = useLocation();
  return <aside className="sidebar">
    <div className="brand-wrap"><div className="brand-mark">P</div><div className="brand-name">PicToText</div></div>
    <nav className="sidebar-nav">{items.map((item) => <Link key={item.path} to={item.path} className={`nav-item ${location.pathname === item.path ? 'active' : ''}`} title={item.label}><span>{item.icon}</span></Link>)}</nav>
    <div className="sidebar-footer" title="설정">⚙</div>
  </aside>;
}
