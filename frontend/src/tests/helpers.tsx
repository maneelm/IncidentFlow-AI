import { vi } from "vitest";
import { render } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import type { ReactNode } from "react";
import { AuthContext } from "../features/auth/context";
import type { User } from "../types/models";
import { user } from "./fixtures";
export function renderPage(
  children: ReactNode,
  options: { actor?: User | null; route?: string; client?: QueryClient } = {},
) {
  const client =
    options.client ??
    new QueryClient({
      defaultOptions: {
        queries: { retry: false, gcTime: 0 },
        mutations: { retry: false },
      },
    });
  return {
    ...render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[options.route ?? "/"]}>
          <AuthContext.Provider
            value={{
              user: options.actor === undefined ? user : options.actor,
              expired: false,
              login: vi.fn(),
              logout: vi.fn(),
            }}
          >
            {children}
          </AuthContext.Provider>
        </MemoryRouter>
      </QueryClientProvider>,
    ),
    client,
  };
}
export function mockApi(
  handler: (
    path: string,
    init: RequestInit | undefined,
  ) => unknown | Promise<unknown>,
) {
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const data = await handler(String(input), init);
    return data instanceof Response
      ? data
      : new Response(JSON.stringify(data), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}
export const failure = (
  status: number,
  code = "http_error",
  details: unknown[] = [],
) =>
  new Response(
    JSON.stringify({ error: { code, message: "Safe backend error", details } }),
    { status, headers: { "Content-Type": "application/json" } },
  );
