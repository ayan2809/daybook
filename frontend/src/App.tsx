import type { ReactNode } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router";
import { MeProvider, useMe } from "./me";
import { Landing } from "./screens/Landing";
import { TodayScreen } from "./screens/Today";
import { Reflect } from "./screens/reflect/Reflect";
import { SummaryScreen } from "./screens/Summary";
import { Timeline } from "./screens/Timeline";
import { ItemDetail } from "./screens/ItemDetail";
import { Ask } from "./screens/Ask";
import { Remind } from "./screens/Remind";
import { SettingsScreen } from "./screens/Settings";
import { AdminOverviewScreen } from "./screens/admin/AdminOverview";
import { AdminTraceScreen } from "./screens/admin/AdminTrace";
import { Loading, Notice } from "./components/Status";

function RequireRole({ owner = false, children }: { owner?: boolean; children: ReactNode }) {
  const { me, loading, error, refresh } = useMe();
  if (loading) return <Loading full />;
  if (!me) {
    return (
      <main className="page">
        <Notice tone="error">
          Can't reach Daybook right now. {error}{" "}
          <button className="link" onClick={() => void refresh()}>
            Try again
          </button>
        </Notice>
      </main>
    );
  }
  if (!me.role) return <Navigate to="/" replace />;
  if (owner && me.role !== "owner") return <Navigate to="/today" replace />;
  return <>{children}</>;
}

function Home() {
  const { me, loading } = useMe();
  if (loading) return <Loading full />;
  if (me?.role) return <Navigate to="/today" replace />;
  return <Landing />;
}

const guarded = (el: ReactNode, owner = false) => <RequireRole owner={owner}>{el}</RequireRole>;

export function App() {
  return (
    <MeProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/today" element={guarded(<TodayScreen />)} />
          <Route path="/reflect" element={guarded(<Reflect />)} />
          <Route path="/summary/:id" element={guarded(<SummaryScreen />)} />
          <Route path="/timeline" element={guarded(<Timeline />)} />
          <Route path="/timeline/:id" element={guarded(<ItemDetail />)} />
          <Route path="/ask" element={guarded(<Ask />)} />
          <Route path="/remind" element={guarded(<Remind />)} />
          <Route path="/settings" element={guarded(<SettingsScreen />)} />
          <Route path="/admin" element={guarded(<AdminOverviewScreen />, true)} />
          <Route path="/admin/sessions/:id" element={guarded(<AdminTraceScreen />, true)} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </MeProvider>
  );
}
