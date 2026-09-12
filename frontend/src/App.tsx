import type { ReactElement, ReactNode } from 'react';
import { Navigate, Route, Routes, useLocation } from 'react-router-dom';

import { getAuthToken } from '@/api/client';
import { CourseDetailPage } from '@/pages/CourseDetailPage';
import { CoursesPage } from '@/pages/CoursesPage';
import { LectureViewerPage } from '@/pages/LectureViewerPage';
import { LoginPage } from '@/pages/LoginPage';
import { RegisterPage } from '@/pages/RegisterPage';

interface RequireAuthProps {
  children: ReactNode;
}

function RequireAuth({ children }: RequireAuthProps): ReactElement {
  const location = useLocation();
  const token = getAuthToken();
  if (token === null) {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }
  return <>{children}</>;
}

function NotFound(): ReactElement {
  return <div className="p-6 text-slate-700">404 — сторінку не знайдено.</div>;
}

export default function App(): ReactElement {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/register" element={<RegisterPage />} />
      <Route
        path="/"
        element={
          <RequireAuth>
            <Navigate to="/courses" replace />
          </RequireAuth>
        }
      />
      <Route
        path="/courses"
        element={
          <RequireAuth>
            <CoursesPage />
          </RequireAuth>
        }
      />
      <Route
        path="/courses/:courseId"
        element={
          <RequireAuth>
            <CourseDetailPage />
          </RequireAuth>
        }
      />
      <Route
        path="/lectures/:lectureId"
        element={
          <RequireAuth>
            <LectureViewerPage />
          </RequireAuth>
        }
      />
      <Route path="*" element={<NotFound />} />
    </Routes>
  );
}
