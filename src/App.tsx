import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AppLayout } from '@/components/layout/AppLayout';
import { SurgeryPage } from '@/features/surgery/SurgeryPage';
import { ReviewPage } from '@/features/review/ReviewPage';
import { CalibrationPage } from '@/features/calibration/CalibrationPage';
import { ArchivePage } from '@/features/archive/ArchivePage';
import { OcrDebugPage } from '@/features/ocr-debug/OcrDebugPage';
import { StartPage } from '@/features/start/StartPage';
import { LandingPage } from '@/features/landing/LandingPage';
import { useSessionStore } from '@/store/sessionStore';
import { ToastProvider } from '@/design-system/components/Toast';

const queryClient = new QueryClient();

function RootRedirect() {
  const { activeSession } = useSessionStore();
  return <Navigate to={activeSession ? '/surgery' : '/landing'} replace />;
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <BrowserRouter>
          <Routes>
            {/* Standalone pages — no app chrome */}
            <Route path="/" element={<RootRedirect />} />
            <Route path="/landing" element={<LandingPage />} />
            <Route path="/start"   element={<StartPage />} />

            {/* Surgery — full-screen, own header */}
            <Route path="/surgery" element={<SurgeryPage />} />

            {/* Clinical app pages — shared top nav */}
            <Route element={<AppLayout />}>
              <Route path="/review"      element={<ReviewPage />} />
              <Route path="/calibration" element={<CalibrationPage />} />
              <Route path="/archive"     element={<ArchivePage />} />
              <Route path="/ocr-debug"   element={<OcrDebugPage />} />
            </Route>

            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </BrowserRouter>
      </ToastProvider>
    </QueryClientProvider>
  );
}
