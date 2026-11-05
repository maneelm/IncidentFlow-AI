import { useState } from "react";
import { Check, Copy } from "lucide-react";
import { date } from "../lib/formatting";

export function Identifier({
  value,
  name = "record ID",
}: {
  value: string;
  name?: string;
}) {
  const [state, setState] = useState<"idle" | "copied" | "unavailable">("idle");
  async function copy() {
    try {
      if (!navigator.clipboard) throw new Error("unavailable");
      await navigator.clipboard.writeText(value);
      setState("copied");
    } catch {
      setState("unavailable");
    }
  }
  return (
    <span className="identifier">
      <code className="mono" title={value} aria-label={`${name}: ${value}`}>
        {value.length > 14 ? `${value.slice(0, 8)}…${value.slice(-4)}` : value}
      </code>
      <button
        type="button"
        className="copy-button"
        onClick={() => void copy()}
        aria-label={`Copy ${name}`}
        title={`Copy full ${name}`}
      >
        {state === "copied" ? <Check size={13} /> : <Copy size={13} />}
      </button>
      <span
        className={state === "unavailable" ? "copy-fallback" : "sr-only"}
        role="status"
      >
        {state === "copied" ? (
          `${name} copied.`
        ) : state === "unavailable" ? (
          <>
            Copy unavailable. Select the full identifier: <code>{value}</code>
          </>
        ) : (
          ""
        )}
      </span>
    </span>
  );
}

export function Timestamp({ value }: { value: string | null | undefined }) {
  if (!value || Number.isNaN(Date.parse(value)))
    return <span className="muted">—</span>;
  return (
    <time
      dateTime={value}
      title={new Date(value).toLocaleString(undefined, {
        year: "numeric",
        month: "long",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        timeZoneName: "short",
      })}
    >
      {date(value)}
    </time>
  );
}
