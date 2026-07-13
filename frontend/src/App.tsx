import { useEffect } from "react";
import { Navigate, Route, Routes } from "react-router-dom";

import { tokens } from "./api/client";
import Layout from "./components/Layout";
import { UserProvider, useUser } from "./context/UserContext";
import Accounts from "./pages/Accounts";
import Holidays from "./pages/Holidays";
import Login from "./pages/Login";
import ScheduleDetail from "./pages/ScheduleDetail";
import ScheduleHistory from "./pages/ScheduleHistory";
import ScheduleSetup from "./pages/ScheduleSetup";
import SchedulesList from "./pages/SchedulesList";

function RequireAuth({ children }: { children: JSX.Element }) {
  if (!tokens().access) return <Navigate to="/login" replace />;
  return children;
}

function RequireManager({ children }: { children: JSX.Element }) {
  const { me, loading } = useUser();
  if (loading) return null;
  if (me && me.role !== "manager_nurse") return <Navigate to="/schedules" replace />;
  return children;
}

/** Wraps an authenticated app page with the app shell (nav bar + main).
 * Ensures the user profile is loaded (e.g. right after login, when the provider
 * mounted before a token existed). */
function Shell({ children }: { children: JSX.Element }) {
  const { me, loading, reload } = useUser();
  useEffect(() => {
    if (!me && !loading && tokens().access) void reload();
  }, [me, loading, reload]);
  return (
    <RequireAuth>
      <Layout>{children}</Layout>
    </RequireAuth>
  );
}

export default function App() {
  return (
    <UserProvider>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/" element={<Navigate to="/schedules" replace />} />
        <Route
          path="/schedules"
          element={
            <Shell>
              <SchedulesList />
            </Shell>
          }
        />
        <Route
          path="/schedules/:id"
          element={
            <Shell>
              <ScheduleDetail />
            </Shell>
          }
        />
        <Route
          path="/schedules/:id/setup"
          element={
            <Shell>
              <RequireManager>
                <ScheduleSetup />
              </RequireManager>
            </Shell>
          }
        />
        <Route
          path="/schedules/:id/history"
          element={
            <Shell>
              <RequireManager>
                <ScheduleHistory />
              </RequireManager>
            </Shell>
          }
        />
        <Route
          path="/accounts"
          element={
            <Shell>
              <RequireManager>
                <Accounts />
              </RequireManager>
            </Shell>
          }
        />
        <Route
          path="/holidays"
          element={
            <Shell>
              <RequireManager>
                <Holidays />
              </RequireManager>
            </Shell>
          }
        />
        <Route path="*" element={<Navigate to="/schedules" replace />} />
      </Routes>
    </UserProvider>
  );
}
