import { Link, useLocation } from 'react-router-dom';

const items = [
  { label: 'Dashboard', icon: '⌂', path: '/dashboard' },
  { label: 'OCR', icon: '◫', path: '/ocr' },
  { label: 'Reports', icon: '▣', path: '/reports' },
  { label: 'My Page', icon: '◉', path: '/mypage' },
];

export default function Sidebar() {
  const location = useLocation();

  return (
    <aside className="sidebar">
      <div className="brand-wrap">
        <div className="brand-mark">P</div>
        <div className="brand-name">PicToText</div>
      </div>

      <nav className="sidebar-nav">
        {items.map((item) => {
          const active = location.pathname === item.path;
          return (
            <Link
              key={item.path}
              to={item.path}
              className={`nav-item ${active ? 'active' : ''}`}
              title={item.label}
            >
              <span>{item.icon}</span>
            </Link>
          );
        })}
      </nav>

      <div className="sidebar-footer">⚙</div>
    </aside>
  );
}
