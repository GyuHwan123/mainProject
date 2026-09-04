import { useEffect, useState } from 'react';
import { Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { getAppUser, hasAppSession } from './features/appSession';
import AuthCallbackPage from './pages/AuthCallbackPage';
import DashboardPage from './pages/DashboardPage';
import LoginPage from './pages/LoginPage';
import ResetPasswordPage from './pages/ResetPasswordPage';
import MyPage from './pages/MyPage';
import OCRPage from './pages/OCRPage';
import ReportPage from './pages/ReportPage';
import ChatPage from './pages/ChatPage';
import PaymentSuccessPage from './pages/PaymentSuccessPage';
import PaymentFailPage from './pages/PaymentFailPage';

function ProtectedRoute({ children }) {
  if (!hasAppSession()) {
    return <Navigate to="/login" replace />;
  }

  return children;
}

function EnterpriseRoute({ children }) {
  if (!hasAppSession()) return <Navigate to="/login" replace />;
  const user = getAppUser();
  const canViewReports = user.subscriptionTier === 'ENTERPRISE' || ['DEVELOPER', 'ADMIN'].includes(user.role);
  if (!canViewReports) return <Navigate to="/dashboard" replace />;
  return children;
}

export default function App() {
  const location = useLocation();
  const isOcrRoute = location.pathname === '/ocr';
  const isReportRoute = location.pathname === '/reports';
  const [ocrMounted, setOcrMounted] = useState(isOcrRoute);
  const [reportMounted, setReportMounted] = useState(isReportRoute);

  useEffect(() => {
    if (isOcrRoute) setOcrMounted(true);
    if (isReportRoute) setReportMounted(true);
  }, [isOcrRoute, isReportRoute]);

  const hasSession = hasAppSession();

  return (
    <>
      {hasSession && (ocrMounted || isOcrRoute) && (
        <div className={`persistent-route ${isOcrRoute ? 'is-active' : ''}`} style={{ display: isOcrRoute ? 'contents' : 'none' }} aria-hidden={!isOcrRoute}><OCRPage /></div>
      )}
      {hasSession && (reportMounted || isReportRoute) && (
        <div className={`persistent-route ${isReportRoute ? 'is-active' : ''}`} style={{ display: isReportRoute ? 'contents' : 'none' }} aria-hidden={!isReportRoute}>
          <EnterpriseRoute><ReportPage /></EnterpriseRoute>
        </div>
      )}
      <Routes>
        <Route path="/" element={<Navigate to="/login" replace />} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="/reset-password" element={<ResetPasswordPage />} />
        <Route path="/auth/callback" element={<AuthCallbackPage />} />
        <Route path="/dashboard" element={<ProtectedRoute><DashboardPage /></ProtectedRoute>} />
        <Route path="/ocr" element={hasSession ? null : <Navigate to="/login" replace />} />
        <Route path="/chat" element={<ProtectedRoute><ChatPage /></ProtectedRoute>} />
        <Route path="/reports" element={hasSession ? null : <Navigate to="/login" replace />} />
        <Route path="/mypage" element={<ProtectedRoute><MyPage /></ProtectedRoute>} />
        <Route path="/payment/success" element={<ProtectedRoute><PaymentSuccessPage /></ProtectedRoute>} />
        <Route path="/payment/fail" element={<ProtectedRoute><PaymentFailPage /></ProtectedRoute>} />
      </Routes>
    </>
  );
}
