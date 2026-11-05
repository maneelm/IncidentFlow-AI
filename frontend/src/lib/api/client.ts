import { session } from "../auth/session";
import type { Params } from "../../types/models";

export interface ValidationDetail {
  location?: (string | number)[];
  message?: string;
  type?: string;
}
export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details: ValidationDetail[] = [],
  ) {
    super(message);
    this.name = "ApiError";
  }
}
export const statusMessage: Record<number, string> = {
  0: "Cannot reach the API. Check your connection and backend process.",
  401: "Your session has expired. Sign in again.",
  403: "Your role does not allow this operation.",
  404: "This resource was not found.",
  409: "The record changed or this operation is no longer valid. Refresh and review before trying again.",
  422: "Some fields are invalid. Review the details below.",
  429: "The request limit was reached. Wait before trying again.",
  503: "This service or provider is currently unavailable.",
};
const base = (import.meta.env.VITE_API_BASE_URL || "/api/v1").replace(
  /\/$/,
  "",
);
if (
  !(base.startsWith("/") && !base.startsWith("//")) &&
  !(
    base.startsWith("https://") &&
    !new URL(base).username &&
    !new URL(base).password
  )
)
  throw new Error(
    "API base must be a relative path or a credential-free HTTPS URL",
  );

export async function request<T>(
  path: string,
  options: {
    method?: string;
    body?: unknown;
    params?: Params;
    signal?: AbortSignal;
    form?: URLSearchParams | FormData;
  } = {},
): Promise<T> {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(options.params ?? {}))
    if (value !== "" && value !== null && value !== undefined)
      query.set(key, String(value));
  const authorization = session.token();
  const revision = session.revision();
  let response: Response;
  try {
    response = await fetch(`${base}${path}${query.size ? `?${query}` : ""}`, {
      method: options.method ?? "GET",
      signal: options.signal,
      cache: "no-store",
      credentials: "omit",
      redirect: "error",
      headers: {
        Accept: "application/json",
        ...(authorization ? { Authorization: `Bearer ${authorization}` } : {}),
        ...(options.body !== undefined
          ? { "Content-Type": "application/json" }
          : {}),
      },
      body:
        options.form ??
        (options.body === undefined ? undefined : JSON.stringify(options.body)),
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError")
      throw error;
    throw new ApiError(0, "network_error", statusMessage[0]);
  }
  if (revision !== session.revision())
    throw new ApiError(
      401,
      "session_changed",
      "Session changed while the request was running.",
    );
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    if (response.status === 401 && authorization && path !== "/auth/token")
      session.expire();
    throw new ApiError(
      response.status,
      data?.error?.code ?? "http_error",
      data?.error?.message ??
        statusMessage[response.status] ??
        "The request failed.",
      data?.error?.details ?? [],
    );
  }
  return data as T;
}
export const api = {
  get: <T>(path: string, params?: Params, signal?: AbortSignal) =>
    request<T>(path, { params, signal }),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body }),
  patch: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "PATCH", body }),
  put: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "PUT", body }),
};
