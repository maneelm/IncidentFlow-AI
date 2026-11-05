let token: string | null = null;
let revision = 0;
const listeners = new Set<() => void>();
export const session = {
  token: () => token,
  revision: () => revision,
  set(value: string | null) {
    token = value;
    revision++;
  },
  expire() {
    token = null;
    revision++;
    for (const listener of listeners) listener();
  },
  subscribe(listener: () => void) {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  },
};
export function safeNext(value: unknown): string {
  return typeof value === "string" &&
    /^\/(?:$|incidents(?:\/|$)|services(?:\/|$)|logs(?:\/|$)|detections(?:\/|$)|investigations(?:\/|$)|runbooks(?:\/|$)|resolutions(?:\/|$)|settings(?:\/|$))/.test(
      value,
    ) &&
    !value.includes("\\")
    ? value
    : "/";
}
