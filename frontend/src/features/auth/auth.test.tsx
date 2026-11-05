import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { render } from "@testing-library/react";
import { AuthProvider } from "./AuthProvider";
import { Login } from "./Login";
import { useAuth } from "./context";
import { ProtectedRoute } from "../../app/App";
import { session } from "../../lib/auth/session";
import { mockApi, failure } from "../../tests/helpers";
import { user } from "../../tests/fixtures";

function Destination() {
  const { user: current, logout } = useAuth();
  return (
    <>
      <h1>{current?.display_name}</h1>
      <button onClick={logout}>Logout now</button>
    </>
  );
}
function setup() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/incidents/protected"]}>
        <AuthProvider>
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route element={<ProtectedRoute />}>
              <Route path="/incidents/protected" element={<Destination />} />
              <Route path="/" element={<Destination />} />
            </Route>
          </Routes>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return client;
}
describe("authentication and protected routing", () => {
  it("uses form login, retrieves the user, preserves deep links and clears on logout", async () => {
    const fetch = mockApi((path) =>
      path.endsWith("/auth/token")
        ? { access_token: "ephemeral", expires_in: 900, token_type: "bearer" }
        : user,
    );
    setup();
    expect(
      screen.getByRole("heading", { name: "Sign in to IncidentFlow" }),
    ).toBeVisible();
    fireEvent.change(screen.getByLabelText("Email address"), {
      target: { value: user.email },
    });
    fireEvent.change(screen.getByLabelText("Password", { exact: true }), {
      target: { value: "only-in-memory" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(
      await screen.findByRole("heading", { name: user.display_name }),
    ).toBeVisible();
    const [path, options] = fetch.mock.calls[0];
    expect(path).toBe("/api/v1/auth/token");
    expect(options?.body).toBeInstanceOf(URLSearchParams);
    expect(String(options?.body)).toContain("username=");
    expect(options?.headers).not.toHaveProperty("Content-Type");
    expect(session.token()).toBe("ephemeral");
    fireEvent.click(screen.getByRole("button", { name: "Logout now" }));
    await screen.findByRole("heading", { name: "Sign in to IncidentFlow" });
    expect(session.token()).toBeNull();
    expect(localStorage.length).toBe(0);
  });
  it("shows invalid credentials and erases the entered password", async () => {
    mockApi(() => failure(401, "invalid_credentials"));
    setup();
    fireEvent.change(screen.getByLabelText("Email address"), {
      target: { value: user.email },
    });
    fireEvent.change(screen.getByLabelText("Password", { exact: true }), {
      target: { value: "wrong-password" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Sign-in failed",
    );
    await waitFor(() =>
      expect(screen.getByLabelText("Password", { exact: true })).toHaveValue(
        "",
      ),
    );
    expect(session.token()).toBeNull();
  });
  it("clears protected state when a session is expired", async () => {
    mockApi((path) =>
      path.endsWith("/auth/token")
        ? { access_token: "ephemeral", expires_in: 900, token_type: "bearer" }
        : user,
    );
    const client = setup();
    fireEvent.change(screen.getByLabelText("Email address"), {
      target: { value: user.email },
    });
    fireEvent.change(screen.getByLabelText("Password", { exact: true }), {
      target: { value: "only-in-memory" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await screen.findByRole("heading", { name: user.display_name });
    client.setQueryData(["private"], { secret: "cached" });
    act(() => session.expire());
    await screen.findByRole("heading", { name: "Sign in to IncidentFlow" });
    expect(screen.getByText(/Your session expired/)).toBeVisible();
    expect(client.getQueryData(["private"])).toBeUndefined();
  });
  it("invalidates at the issued token deadline without inventing refresh", async () => {
    vi.useFakeTimers();
    const fetch = mockApi((path) =>
      path.endsWith("/auth/token")
        ? { access_token: "short-lived", expires_in: 1, token_type: "bearer" }
        : user,
    );
    setup();
    fireEvent.change(screen.getByLabelText("Email address"), {
      target: { value: user.email },
    });
    fireEvent.change(screen.getByLabelText("Password", { exact: true }), {
      target: { value: "memory-only" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30);
    });
    expect(session.token()).toBe("short-lived");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(session.token()).toBeNull();
    expect(
      fetch.mock.calls.some(([path]) => String(path).includes("refresh")),
    ).toBe(false);
  });
});
