import {
  useEffect,
  useId,
  useRef,
  cloneElement,
  isValidElement,
  type ButtonHTMLAttributes,
  type ReactNode,
} from "react";
import {
  AlertCircle,
  ArrowLeft,
  ArrowRight,
  LoaderCircle,
  RefreshCw,
  X,
} from "lucide-react";
import { ApiError, statusMessage } from "../lib/api/client";
import { label } from "../lib/formatting";

export function Button({
  children,
  variant = "default",
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "default" | "primary" | "danger" | "ghost";
}) {
  return (
    <button className={`button ${variant} ${className}`} {...props}>
      {children}
    </button>
  );
}
export function Badge({
  value,
  synthetic = false,
}: {
  value: string;
  synthetic?: boolean;
}) {
  return (
    <span className={`badge ${synthetic ? "synthetic" : value}`}>
      <span className="badge-dot" />
      {label(value)}
    </span>
  );
}
export function PageTitle({
  eyebrow = "OPERATIONS",
  title,
  description,
  actions,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="page-title">
      <div>
        <p className="eyebrow">{eyebrow}</p>
        <h1>{title}</h1>
        {description && <p className="muted">{description}</p>}
      </div>
      <div className="actions">{actions}</div>
    </div>
  );
}
export function Panel({
  title,
  children,
  aside,
  className = "",
}: {
  title?: string;
  children: ReactNode;
  aside?: ReactNode;
  className?: string;
}) {
  return (
    <section className={`panel ${className}`}>
      {title && (
        <div className="panel-heading">
          <h2>{title}</h2>
          {aside}
        </div>
      )}
      <div className="panel-body">{children}</div>
    </section>
  );
}
export function Loading({ text = "Loading records…" }: { text?: string }) {
  return (
    <div className="state" role="status">
      <LoaderCircle className="spin" size={19} />
      <span>{text}</span>
    </div>
  );
}
export function Empty({
  title = "No records found",
  children,
}: {
  title?: string;
  children?: ReactNode;
}) {
  return (
    <div className="empty">
      <div className="empty-mark" aria-hidden="true">
        —
      </div>
      <h3>{title}</h3>
      <p className="muted">
        {children ?? "Try another filter or create your first record."}
      </p>
    </div>
  );
}
export function ErrorPanel({
  error,
  retry,
}: {
  error: unknown;
  retry?: () => void;
}) {
  if (!error) return null;
  const value =
    error instanceof ApiError
      ? error
      : new ApiError(
          0,
          "unknown_error",
          "Something went wrong. Try refreshing this view.",
        );
  return (
    <div className="error-box" role="alert">
      <AlertCircle size={18} />
      <div>
        <strong>
          {value.code === "invalid_credentials"
            ? "Sign-in failed. Check your email and password."
            : (statusMessage[value.status] ??
              "The request could not be completed.")}
        </strong>
        <p>{value.message}</p>
        {value.status === 0 && (
          <p>
            If this was a change request, its outcome may be unknown. Refresh
            the record before any manual retry.
          </p>
        )}
        {value.details.length > 0 && (
          <ul>
            {value.details.map((item, index) => (
              <li key={index}>
                {item.location?.join(" → ")}:{" "}
                {item.message ?? item.type ?? "Invalid field"}
              </li>
            ))}
          </ul>
        )}
        {value.status === 409 && (
          <p>
            Your changes were not replayed. Refresh, compare the current
            version, then reopen the form.
          </p>
        )}
        {retry && (
          <Button type="button" onClick={retry}>
            <RefreshCw size={14} />
            Refresh view
          </Button>
        )}
      </div>
    </div>
  );
}
export function Notice({
  children,
  tone = "info",
}: {
  children: ReactNode;
  tone?: "info" | "warning" | "success";
}) {
  return (
    <div className={`notice ${tone}`}>
      <AlertCircle size={16} />
      <div>{children}</div>
    </div>
  );
}
export function Field({
  label: name,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  const id = useId();
  const control = isValidElement<{
    "aria-labelledby"?: string;
    "aria-describedby"?: string;
  }>(children)
    ? cloneElement(children, {
        "aria-labelledby": id,
        "aria-describedby": hint ? `${id}-hint` : undefined,
      })
    : children;
  return (
    <label className="field">
      <span id={id}>{name}</span>
      {control}
      {hint && <small id={`${id}-hint`}>{hint}</small>}
    </label>
  );
}
export function Json({ value }: { value: unknown }) {
  return <pre className="json">{JSON.stringify(value, null, 2)}</pre>;
}
export function Pagination({
  offset,
  total,
  limit = 20,
  onChange,
}: {
  offset: number;
  total: number;
  limit?: number;
  onChange: (next: number) => void;
}) {
  return (
    <div className="pagination">
      <span>
        {total
          ? `${offset + 1}–${Math.min(offset + limit, total)} of ${total.toLocaleString()}`
          : "0 records"}
      </span>
      <div className="actions">
        <Button
          aria-label="Previous page"
          disabled={offset === 0}
          onClick={() => onChange(Math.max(0, offset - limit))}
        >
          <ArrowLeft size={15} />
        </Button>
        <Button
          aria-label="Next page"
          disabled={offset + limit >= total || offset + limit > 100000}
          onClick={() => onChange(offset + limit)}
        >
          <ArrowRight size={15} />
        </Button>
      </div>
    </div>
  );
}
export function Dialog({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const id = useId();
  useEffect(() => {
    const element = ref.current;
    const previousFocus =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    element?.showModal();
    return () => {
      element?.close();
      if (previousFocus?.isConnected) previousFocus.focus();
    };
  }, []);
  return (
    <dialog
      ref={ref}
      className="dialog"
      aria-labelledby={id}
      onCancel={onClose}
    >
      <div className="dialog-title">
        <h2 id={id}>{title}</h2>
        <Button
          variant="ghost"
          type="button"
          onClick={onClose}
          aria-label="Close dialog"
        >
          <X size={18} />
        </Button>
      </div>
      <div className="dialog-body">{children}</div>
    </dialog>
  );
}
export function Submit({
  pending,
  children = "Save changes",
}: {
  pending: boolean;
  children?: ReactNode;
}) {
  return (
    <Button type="submit" variant="primary" disabled={pending}>
      {pending && <LoaderCircle size={15} className="spin" />}
      {children}
    </Button>
  );
}
export function DataTable({
  headers,
  children,
  label: name,
}: {
  headers: string[];
  children: ReactNode;
  label: string;
}) {
  return (
    <div
      className="table-scroll"
      tabIndex={0}
      role="region"
      aria-label={`${name} table`}
    >
      <table aria-label={name}>
        <thead>
          <tr>
            {headers.map((header) => (
              <th key={header} scope="col">
                {header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}
