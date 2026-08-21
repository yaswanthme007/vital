import { useEffect } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AppLayout } from '@/components/layout/AppLayout';
import { OperationPage } from '@/features/operation/OperationPage';
import { CameraCaptureController } from '@/features/operation/CameraCaptureController';
import { ReviewPage } from '@/features/review/ReviewPage';
import { CalibrationPage } from '@/features/calibration/CalibrationPage';
import { ArchivePage } from '@/features/archive/ArchivePage';
import { OcrDebugPage } from '@/features/ocr-debug/OcrDebugPage';
import { StartPage } from '@/features/start/StartPage';
import { LandingPage } from '@/features/landing/LandingPage';
import { useSessionStore } from '@/store/sessionStore';
import { useVitalsSimulation } from '@/hooks/useVitalsSimulation';
import { ToastProvider } from '@/design-system/components/Toast';

const queryClient = new QueryClient();

function RootRedirect() {
  const { activeSession } = useSessionStore();
  return <Navigate to={activeSession ? '/operation' : '/landing'} replace />;
}

/**
 * M5.7. Mounted once, ABOVE <Routes> — see CameraCaptureController's own
 * docstring for why: the vitals WebSocket connection and the camera
 * capture/push loop must both survive client-side navigation (Operation ->
 * Review -> Archive -> back) rather than being torn down and reopened by
 * whichever page happens to be mounted, which is what silently paused
 * observation before M5.7. AppLayout and OperationPage therefore no longer
 * call useVitalsSimulation() themselves — there must be exactly one
 * instance of this connection per session, owned here.
 */
function AppShell() {
  const rehydrateActiveSession = useSessionStore((s) => s.rehydrateActiveSession);
  const hydrated = useSessionStore((s) => s.hydrated);

  // M5.7 (F6, durability): re-confirm a session persisted in localStorage
  // against the backend once, on first mount, BEFORE the camera/WS owners
  // below act on it — see rehydrateActiveSession's own docstring.
  useEffect(() => {
    void rehydrateActiveSession();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useVitalsSimulation();

  if (!hydrated) return null;

  return (
    <>
      <CameraCaptureController />
      <Routes>
        {/* Standalone pages — no app chrome */}
        <Route path="/" element={<RootRedirect />} />
        <Route path="/landing" element={<LandingPage />} />
        <Route path="/start"   element={<StartPage />} />

        {/* Active Operation — full-screen, own header */}
        <Route path="/operation" element={<OperationPage />} />
        {/* Pre-M5.7 route name, kept as a redirect for anything still linking it. */}
        <Route path="/surgery" element={<Navigate to="/operation" replace />} />

        {/* Clinical app pages — shared top nav */}
        <Route element={<AppLayout />}>
          {/* M5.8.1: /review/:sessionId is the addressable, refresh-safe
              link to a specific completed operation's Review & Sign-off
              (End Operation, Archive's "Continue to Review" both navigate
              here). Bare /review (persistent nav link, no id in the URL)
              falls back to sessionStore's lastEndedSessionId -- see
              ReviewPage. */}
          <Route path="/review"              element={<ReviewPage />} />
          <Route path="/review/:sessionId"   element={<ReviewPage />} />
          <Route path="/calibration" element={<CalibrationPage />} />
          <Route path="/archive"     element={<ArchivePage />} />
          <Route path="/ocr-debug"   element={<OcrDebugPage />} />
        </Route>

        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <BrowserRouter>
          <AppShell />
        </BrowserRouter>
      </ToastProvider>
    </QueryClientProvider>
  );
}
