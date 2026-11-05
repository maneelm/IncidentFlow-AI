import { useState, type FormEvent } from "react";
import { Activity, ArrowRight, Fingerprint, ShieldCheck } from "lucide-react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "./context";
import { safeNext } from "../../lib/auth/session";
import { ErrorPanel, Field, Notice, Submit } from "../../components/ui";

export function Login() {
  const { user, login, expired } = useAuth();
  const [error, setError] = useState<unknown>(null);
  const [pending, setPending] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  if (user)
    return (
      <Navigate
        to={safeNext((location.state as { from?: unknown } | null)?.from)}
        replace
      />
    );
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    setPending(true);
    setError(null);
    try {
      await login(String(values.get("email")), String(values.get("password")));
      form.reset();
      navigate(safeNext((location.state as { from?: unknown } | null)?.from), {
        replace: true,
      });
    } catch (failure) {
      setError(failure);
      const input = form.elements.namedItem("password") as HTMLInputElement;
      input.value = "";
    } finally {
      setPending(false);
    }
  }
  return (
    <main className="login">
      <div className="login-story">
        <div className="brand">
          <span className="brand-icon">
            <Activity size={25} />
          </span>
          <span>
            incidentflow<span className="brand-ai">AI</span>
          </span>
        </div>
        <div>
          <p className="eyebrow">INCIDENT OPERATIONS</p>
          <h1>
            From first signal
            <br />
            to a reviewed resolution.
          </h1>
          <p className="muted">
            One workspace for incident evidence, AI-assisted investigation, and
            human-approved synthetic workflows.
          </p>
          <div className="login-flow">
            <span>Detect</span>
            <ArrowRight size={16} />
            <span>Investigate</span>
            <ArrowRight size={16} />
            <span>Review</span>
          </div>
        </div>
        <p className="muted small">
          <ShieldCheck size={15} /> Authorization enforced by the backend
        </p>
      </div>
      <section className="login-panel">
        <div className="login-form">
          <div className="login-symbol">
            <Fingerprint size={27} />
          </div>
          <p className="eyebrow">YOUR OPERATIONS WORKSPACE</p>
          <h2>Sign in to IncidentFlow</h2>
          <p className="muted">Use your operator-provisioned account.</p>
          {expired && (
            <Notice tone="warning">
              Your session expired. Sign in again to continue.
            </Notice>
          )}
          <ErrorPanel error={error} />
          <form onSubmit={submit}>
            <Field label="Email address">
              <input
                name="email"
                type="email"
                autoComplete="username"
                placeholder="you@company.com"
                required
                maxLength={320}
              />
            </Field>
            <Field label="Password">
              <input
                name="password"
                type="password"
                autoComplete="current-password"
                required
                maxLength={1024}
              />
            </Field>
            <Submit pending={pending}>
              Sign in <ArrowRight size={16} />
            </Submit>
          </form>
          <p className="session-note">
            <ShieldCheck size={15} /> Access tokens stay in memory. Reloading
            requires a new sign-in.
          </p>
        </div>
      </section>
    </main>
  );
}
