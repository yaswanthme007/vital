import { Outlet } from 'react-router-dom';
import { TopNav } from './TopNav';
import { useVitalsSimulation } from '@/hooks/useVitalsSimulation';

export function AppLayout() {
  useVitalsSimulation();

  return (
    <div className="flex flex-col h-screen w-screen overflow-hidden bg-clinical-bg">
      <TopNav />
      <main className="flex-1 overflow-hidden min-h-0">
        <Outlet />
      </main>
    </div>
  );
}
