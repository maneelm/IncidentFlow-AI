import { describe, expect, it, vi } from "vitest";
import { QueryClient } from "@tanstack/react-query";
import { api, ApiError, request } from "./client";
import { session, safeNext } from "../auth/session";
import { queryClient, activeInvestigation, activeProposal } from "./query";
import { failure, mockApi } from "../../tests/helpers";

describe("API boundary", () => {
  it("encodes filters, keeps bearer auth out of URLs, and disables caching", async () => {
    session.set("memory-only-token");
    const fetch = mockApi(() => ({ items: [] }));
    await api.get("/logs", {
      search: "timeout & trace",
      offset: 20,
      level: "error",
    });
    const [path, options] = fetch.mock.calls[0];
    expect(String(path)).toContain("search=timeout+%26+trace");
    expect(path).not.toContain("memory-only-token");
    expect(options).toMatchObject({
      cache: "no-store",
      credentials: "omit",
      redirect: "error",
      headers: { Authorization: "Bearer memory-only-token" },
    });
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });
  it.each([401, 403, 404, 409, 422, 429, 503])(
    "preserves HTTP %s and validation locations",
    async (status) => {
      mockApi(() =>
        failure(status, "safe_code", [
          { location: ["body", "title"], message: "Invalid field" },
        ]),
      );
      await expect(api.get("/incidents")).rejects.toMatchObject({
        status,
        code: "safe_code",
        details: [{ location: ["body", "title"], message: "Invalid field" }],
      });
    },
  );
  it("invalidates the current memory session on an authenticated 401", async () => {
    session.set("old-token");
    const expired = vi.fn();
    const unsubscribe = session.subscribe(expired);
    mockApi(() => failure(401));
    await expect(api.get("/auth/me")).rejects.toBeInstanceOf(ApiError);
    expect(session.token()).toBeNull();
    expect(expired).toHaveBeenCalledOnce();
    unsubscribe();
  });
  it("does not expire a new session when an older response arrives", async () => {
    session.set("old");
    let resolve!: (value: unknown) => void;
    mockApi(
      () =>
        new Promise((value) => {
          resolve = value;
        }),
    );
    const pending = api.get("/incidents");
    session.set("new");
    resolve(failure(401));
    await expect(pending).rejects.toMatchObject({ code: "session_changed" });
    expect(session.token()).toBe("new");
  });
  it("reports network failures and never replays POST", async () => {
    const fetch = mockApi(() => {
      throw new TypeError("Connection lost");
    });
    await expect(api.post("/incidents", {})).rejects.toMatchObject({
      status: 0,
      code: "network_error",
    });
    expect(fetch).toHaveBeenCalledTimes(1);
  });
  it("propagates abort cancellation", async () => {
    mockApi(() => {
      throw new DOMException("Cancelled", "AbortError");
    });
    await expect(
      request("/logs", { signal: new AbortController().signal }),
    ).rejects.toMatchObject({ name: "AbortError" });
  });
  it("bounds read retries and does not retry unavailable providers", async () => {
    const client = new QueryClient({
      defaultOptions: {
        queries: { ...queryClient.getDefaultOptions().queries, retryDelay: 0 },
      },
    });
    const read = vi
      .fn()
      .mockRejectedValue(new ApiError(0, "network", "offline"));
    await expect(
      client.fetchQuery({ queryKey: ["read"], queryFn: read }),
    ).rejects.toBeInstanceOf(ApiError);
    expect(read).toHaveBeenCalledTimes(2);
    const unavailable = vi
      .fn()
      .mockRejectedValue(new ApiError(503, "unavailable", "provider"));
    await expect(
      client.fetchQuery({ queryKey: ["provider"], queryFn: unavailable }),
    ).rejects.toBeInstanceOf(ApiError);
    expect(unavailable).toHaveBeenCalledTimes(1);
    client.clear();
  });
  it.each([
    "https://evil.example",
    "//evil.example",
    "/\\evil",
    "javascript:alert(1)",
    "/login",
  ])("rejects unsafe or unsupported redirect %s", (value) =>
    expect(safeNext(value)).toBe("/"),
  );
  it("allows known internal deep links", () =>
    expect(safeNext("/incidents/abc?tab=timeline")).toBe(
      "/incidents/abc?tab=timeline",
    ));
  it("stops status polling for terminal resources", () => {
    expect(activeInvestigation({ status: "running" })).toBe(true);
    expect(activeInvestigation({ status: "completed" })).toBe(false);
    expect(activeProposal({ status: "pending_approval" })).toBe(true);
    expect(activeProposal({ status: "succeeded" })).toBe(false);
  });
});
