import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Route, Routes, useLocation } from "react-router-dom";

import { isDemoMode, subscribeDemoMode } from "./api/client";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { Shell } from "./components/Shell";
import Backtesting from "./pages/Backtesting";
import DeskPage from "./pages/DeskPage";
import ModelDocPage from "./pages/ModelDocPage";
import Overview from "./pages/Overview";
import Scenarios from "./pages/Scenarios";
import WhatIf from "./pages/WhatIf";

// retries would just re-hit an immutable batch result; fail fast and show the
// error (exported so tests can clear the cache between cases). Focus refetch
// stays on: it only fires for stale queries, so pinned (staleTime Infinity)
// views never refetch while a parked tab catches the nightly batch.
export const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false } },
});

// leaving demo mode means cached entries may still hold snapshot payloads:
// refetch everything so live and snapshot numbers never mix on screen
subscribeDemoMode(() => {
  if (!isDemoMode()) void queryClient.invalidateQueries();
});

// keyed by location so navigating (or re-pinning as_of) replaces the boundary:
// without the key React reuses the one instance across routes and a caught
// error would stick to every page until a hard reload
function Page({ children }: React.PropsWithChildren) {
  const loc = useLocation();
  return <ErrorBoundary key={loc.pathname + loc.search}>{children}</ErrorBoundary>;
}

const page = (el: React.ReactNode) => <Page>{el}</Page>;

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route element={<Shell />}>
            <Route path="/" element={page(<Overview />)} />
            <Route path="/desks/:deskCode" element={page(<DeskPage />)} />
            <Route path="/backtesting" element={page(<Backtesting />)} />
            <Route path="/scenarios" element={page(<Scenarios />)} />
            <Route path="/whatif" element={page(<WhatIf />)} />
            <Route path="/docs" element={page(<ModelDocPage />)} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
