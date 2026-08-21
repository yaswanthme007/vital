import { Outlet } from 'react-router-dom';
import { TopNav } from './TopNav';

// M5.7: useVitalsSimulation() moved to src/App.tsx's AppShell, mounted
// once above <Routes> rather than here -- this component (and the pages
// it wraps: Review/Calibration/Archive/OCR Debug) is torn down and
// remounted by every route change, which used to open a fresh WS
// connection each time and silently pause observation while navigating.
export function AppLayout() {
  return (
    <div className="flex flex-col h-screen w-screen overflow-hidden bg-clinical-bg">
      <TopNav />
      <main className="flex-1 overflow-hidden min-h-0">
        <Outlet />
      </main>
    </div>
  );
}
