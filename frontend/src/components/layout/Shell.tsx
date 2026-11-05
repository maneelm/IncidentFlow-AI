import { useState } from "react";
import {
  Activity,
  BookOpen,
  Boxes,
  ChevronRight,
  CircleHelp,
  ClipboardCheck,
  FlaskConical,
  LayoutDashboard,
  LogOut,
  Menu,
  Radio,
  SearchCode,
  ShieldCheck,
  Terminal,
} from "lucide-react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "../../features/auth/context";
import { useApi } from "../../lib/api/query";
import { Button, Dialog } from "../ui";

const navigation = [
  { label: "Overview", to: "/", icon: LayoutDashboard },
  { label: "Incidents", to: "/incidents", icon: Activity },
  { label: "Log explorer", to: "/logs", icon: Terminal },
  { label: "Detection monitoring", to: "/detections", icon: Radio },
  { label: "Investigations", to: "/investigations", icon: SearchCode },
  { label: "Runbooks", to: "/runbooks", icon: BookOpen },
  { label: "Resolution center", to: "/resolutions", icon: ClipboardCheck },
  { label: "Services", to: "/services", icon: Boxes },
];
export function Shell() {
  const { user, logout } = useAuth();
  const [mobile, setMobile] = useState(false);
  const location = useLocation();
  const ready = useApi<{ status: string }>("/ready", undefined, {
    poll: true,
    pollMs: 15000,
  });
  const section =
    navigation.find((item) =>
      item.to === "/"
        ? location.pathname === "/"
        : location.pathname.startsWith(item.to),
    )?.label ?? "Workspace";
  const links = (
    <>
      <p className="nav-label">WORKSPACE</p>
      <nav aria-label="Primary navigation">
        {navigation.map(({ label, to, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === "/"}
            onClick={() => setMobile(false)}
          >
            <Icon size={18} />
            <span>{label}</span>
          </NavLink>
        ))}
      </nav>
      <div className="sidebar-footer">
        <Link to="/guide" onClick={() => setMobile(false)}>
          <CircleHelp size={17} /> Workflow guide
        </Link>
        <div className="environment">
          <FlaskConical size={15} />
          <span>Simulation-enabled backend</span>
        </div>
        <p>No production remediation tools</p>
      </div>
    </>
  );
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <aside className="sidebar">
        <Link className="brand" to="/">
          <span className="brand-icon">
            <Activity size={23} />
          </span>
          <span>
            incidentflow<span className="brand-ai">AI</span>
          </span>
        </Link>
        {links}
      </aside>
      <div className="workspace">
        <header className="topbar">
          <div className="breadcrumb">
            <Button
              variant="ghost"
              aria-label="Open navigation"
              className="mobile-toggle"
              onClick={() => setMobile(true)}
            >
              <Menu size={21} />
            </Button>
            <span>Workspace</span>
            <ChevronRight size={14} />
            <strong>{section}</strong>
          </div>
          <div className="topbar-right">
            <span
              className={`connection ${ready.isError ? "offline" : ""}`}
              role="status"
            >
              <span />
              {ready.isError
                ? "API unavailable"
                : ready.isPending
                  ? "Connecting"
                  : "API ready"}
            </span>
            <span className="divider" />
            <div className="user-avatar" aria-hidden="true">
              {user?.display_name.slice(0, 2).toUpperCase()}
            </div>
            <div className="user-meta">
              <strong>{user?.display_name}</strong>
              <span>
                <ShieldCheck size={11} />
                {user?.role}
              </span>
            </div>
            <Button variant="ghost" onClick={logout} aria-label="Sign out">
              <LogOut size={17} />
            </Button>
          </div>
        </header>
        <main id="main" className="main-content" tabIndex={-1}>
          <Outlet />
        </main>
        <footer className="workspace-footer">
          <span>IncidentFlow / Operations console</span>
          <span>Human decisions. Traceable actions.</span>
        </footer>
      </div>
      {mobile && (
        <Dialog title="Navigation" onClose={() => setMobile(false)}>
          {links}
        </Dialog>
      )}
    </div>
  );
}
