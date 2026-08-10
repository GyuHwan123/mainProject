import { useEffect, useState } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';
import apiClient from './api/client';
import DashboardPage from './pages/DashboardPage';
import LoginPage from './pages/LoginPage';
import MyPage from './pages/MyPage';
import OCRPage from './pages/OCRPage';
import ReportPage from './pages/ReportPage';
import { supabase } from './lib/supabase';

function syncAppSessionWithSupabase() {
  if (!supabase) return;

  const hasLocalToken = Boolean(localStorage.getItem('pic_to_text_token'));
  if (hasLocalToken) return;

  supabase.auth.getSession().then(({ data }) => {
    const session = data?.session;
    if (!session) return;

    apiClient
      .post('/auth/social-login', {
        provider: 'supabase',
        token: session.access_token,
      })
      .then((res) => {
        localStorage.setItem('pic_to_text_token', res.data.access_token);
        localStorage.setItem('pic_to_text_email', res.data.user_email);
      })
      .catch(() => {
        // Ignore social-session exchange failures here; login page handles user feedback.
      });
  });
}

function ProtectedRoute({ children }) {
  const [ready, setReady] = useState(false);
  const [isAuthenticated, setIsAuthenticated] = useState(false);

  useEffect(() => {
    let mounted = true;

    const localToken = localStorage.getItem('pic_to_text_token');
    if (localToken) {
      setIsAuthenticated(true);
      setReady(true);
      return;
    }

    if (!supabase) {
      setReady(true);
      setIsAuthenticated(false);
      return;
    }

    const checkSession = async () => {
      const { data } = await supabase.auth.getSession();
      if (!mounted) return;
      setIsAuthenticated(Boolean(data.session));
      setReady(true);
    };

    checkSession();

    const { data: authListener } = supabase.auth.onAuthStateChange((_event, session) => {
      if (!mounted) return;
      setIsAuthenticated(Boolean(session));
      setReady(true);

      if (session && !localStorage.getItem('pic_to_text_token')) {
        syncAppSessionWithSupabase();
      }
    });

    return () => {
      mounted = false;
      authListener.subscription.unsubscribe();
    };
  }, []);

  if (!ready) {
    return null;
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" replace />;
  }

  return children;
}

export default function App() {
  useEffect(() => {
    syncAppSessionWithSupabase();
  }, []);

  return (
    <Routes>
      <Route path="/" element={<Navigate to="/login" replace />} />
      <Route path="/login" element={<LoginPage />} />
      <Route
        path="/dashboard"
        element={
          <ProtectedRoute>
            <DashboardPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/ocr"
        element={
          <ProtectedRoute>
            <OCRPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/reports"
        element={
          <ProtectedRoute>
            <ReportPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/mypage"
        element={
          <ProtectedRoute>
            <MyPage />
          </ProtectedRoute>
        }
      />
    </Routes>
  );
}
