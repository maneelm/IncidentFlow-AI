import { Component, Suspense, lazy, type ReactNode } from "react";
import { Navigate, Outlet, Route, Routes, useLocation } from "react-router-dom";
import { Shell } from "../components/layout/Shell";
import { useAuth } from "../features/auth/context";
import { Login } from "../features/auth/Login";
import { Loading, PageTitle, Panel } from "../components/ui";
const Overview = lazy(() =>
  import("../features/overview/Overview").then((module) => ({
    default: module.Overview,
  })),
);
const IncidentList = lazy(() =>
  import("../features/incidents/IncidentList").then((module) => ({
    default: module.IncidentList,
  })),
);
const IncidentDetail = lazy(() =>
  import("../features/incidents/IncidentDetail").then((module) => ({
    default: module.IncidentDetail,
  })),
);
const Services = lazy(() =>
  import("../features/services/Services").then((module) => ({
    default: module.Services,
  })),
);
const ServiceDetail = lazy(() =>
  import("../features/services/Services").then((module) => ({
    default: module.ServiceDetail,
  })),
);
const Logs = lazy(() =>
  import("../features/logs/Logs").then((module) => ({ default: module.Logs })),
);
const LogDetail = lazy(() =>
  import("../features/logs/LogDetail").then((module) => ({
    default: module.LogDetail,
  })),
);
const Detections = lazy(() =>
  import("../features/detections/Detections").then((module) => ({
    default: module.Detections,
  })),
);
const DetectionDetail = lazy(() =>
  import("../features/detections/Detections").then((module) => ({
    default: module.DetectionDetail,
  })),
);
const InvestigationList = lazy(() =>
  import("../features/investigations/Investigations").then((module) => ({
    default: module.InvestigationList,
  })),
);
const InvestigationDetail = lazy(() =>
  import("../features/investigations/Investigations").then((module) => ({
    default: module.InvestigationDetail,
  })),
);
const Runbooks = lazy(() =>
  import("../features/runbooks/Runbooks").then((module) => ({
    default: module.Runbooks,
  })),
);
const RunbookDetail = lazy(() =>
  import("../features/runbooks/Runbooks").then((module) => ({
    default: module.RunbookDetail,
  })),
);
const ProposalList = lazy(() =>
  import("../features/resolutions/Resolutions").then((module) => ({
    default: module.ProposalList,
  })),
);
const ProposalDetail = lazy(() =>
  import("../features/resolutions/Resolutions").then((module) => ({
    default: module.ProposalDetail,
  })),
);
const Guide = lazy(() =>
  import("../features/guide/Guide").then((module) => ({
    default: module.Guide,
  })),
);
export function ProtectedRoute() {
  const { user } = useAuth();
  const location = useLocation();
  return user ? (
    <Outlet />
  ) : (
    <Navigate
      to="/login"
      replace
      state={{ from: location.pathname + location.search }}
    />
  );
}
export class ErrorBoundary extends Component<
  { children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidCatch() {
    /* Never log sensitive UI state. */
  }
  render() {
    return this.state.failed ? (
      <main className="main-content">
        <PageTitle title="This view could not be rendered" />
        <Panel>
          <p>
            Reload the application and sign in again. No change request has been
            replayed.
          </p>
          <button className="button" onClick={() => window.location.reload()}>
            Reload application
          </button>
        </Panel>
      </main>
    ) : (
      this.props.children
    );
  }
}
export function App() {
  return (
    <ErrorBoundary>
      <Suspense fallback={<Loading text="Opening workspace…" />}>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route element={<ProtectedRoute />}>
            <Route element={<Shell />}>
              <Route index element={<Overview />} />
              <Route path="incidents" element={<IncidentList />} />
              <Route path="incidents/:id" element={<IncidentDetail />} />
              <Route path="services" element={<Services />} />
              <Route path="services/:id" element={<ServiceDetail />} />
              <Route path="logs" element={<Logs />} />
              <Route path="logs/:id" element={<LogDetail />} />
              <Route path="detections" element={<Detections />} />
              <Route path="detections/:id" element={<DetectionDetail />} />
              <Route path="investigations" element={<InvestigationList />} />
              <Route
                path="investigations/:id"
                element={<InvestigationDetail />}
              />
              <Route path="runbooks" element={<Runbooks />} />
              <Route path="runbooks/:id" element={<RunbookDetail />} />
              <Route path="resolutions" element={<ProposalList />} />
              <Route path="resolutions/:id" element={<ProposalDetail />} />
              <Route path="guide" element={<Guide />} />
              <Route
                path="*"
                element={
                  <Panel>
                    <h1>Page not found</h1>
                    <a href="/">Return to overview</a>
                  </Panel>
                }
              />
            </Route>
          </Route>
        </Routes>
      </Suspense>
    </ErrorBoundary>
  );
}
