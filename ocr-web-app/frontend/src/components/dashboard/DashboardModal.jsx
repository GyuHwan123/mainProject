import { FiX } from 'react-icons/fi';
export default function DashboardModal({ title, description, children, onClose }) {
  return <div className="dashboard-modal-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}><section className="dashboard-modal" role="dialog" aria-modal="true" aria-label={title}><header><div><h2>{title}</h2>{description && <p>{description}</p>}</div><button type="button" onClick={onClose} aria-label="닫기"><FiX /></button></header>{children}</section></div>;
}
