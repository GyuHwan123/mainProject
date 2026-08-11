import { Link, useLocation } from 'react-router-dom';

import { IoDocumentTextOutline } from 'react-icons/io5';
import { RiUser3Fill } from "react-icons/ri";
import { MdHomeFilled } from "react-icons/md";
import { GrCatalogOption } from "react-icons/gr";


const items = [
  { label: '대시보드', icon: <MdHomeFilled />, path: '/dashboard' },
  { label: 'PDF 추출', icon: <IoDocumentTextOutline />, path: '/ocr' },
  { label: 'AI 채팅', icon: '✦', path: '/chat' },
  { label: '리포트', icon: <GrCatalogOption />, path: '/reports' },
  { label: '내 정보', icon: <RiUser3Fill />, path: '/mypage' },
];

export default function Sidebar() {
  const location = useLocation();
  return <aside className="sidebar">
    <div className="brand-wrap"><div className="brand-mark">P</div><div className="brand-name">PicToText</div></div>
    <nav className="sidebar-nav">{items.map((item) => <Link key={item.path} to={item.path} className={`nav-item ${location.pathname === item.path ? 'active' : ''}`} title={item.label}><span>{item.icon}</span></Link>)}</nav>
    <div className="sidebar-footer" title="설정">⚙</div>
  </aside>;
}
