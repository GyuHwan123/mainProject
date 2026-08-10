export default function TopBar({ title = 'Dashboard', actions = null }) {
  return (
    <header className="topbar">
      <div className="topbar-title">{title}</div>
      <div className="topbar-actions">{actions}</div>
    </header>
  );
}
