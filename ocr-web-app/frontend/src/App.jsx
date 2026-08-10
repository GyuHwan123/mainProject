import { Navigate, Route, Routes } from 'react-router-dom';
import { hasAppSession } from './features/appSession';
import AuthCallbackPage from './pages/AuthCallbackPage';
import DashboardPage from './pages/DashboardPage';
import LoginPage from './pages/LoginPage';
import MyPage from './pages/MyPage';
import OCRPage from './pages/OCRPage';
import ReportPage from './pages/ReportPage';
import ChatPage from './pages/ChatPage';

function ProtectedRoute({ children }) {
  if (!hasAppSession()) {
    return <Navigate to="/login" replace />;
  }

  return children;
}

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/login" replace />} />
      <Route path="/login" element={<LoginPage />} />
      <Route path="/auth/callback" element={<AuthCallbackPage />} />
      <Route path="/dashboard" element={<ProtectedRoute><DashboardPage /></ProtectedRoute>} />
      <Route path="/ocr" element={<ProtectedRoute><OCRPage /></ProtectedRoute>} />
      <Route path="/chat" element={<ProtectedRoute><ChatPage /></ProtectedRoute>} />
      <Route path="/reports" element={<ProtectedRoute><ReportPage /></ProtectedRoute>} />
      <Route path="/mypage" element={<ProtectedRoute><MyPage /></ProtectedRoute>} />
    </Routes>
  );
}
