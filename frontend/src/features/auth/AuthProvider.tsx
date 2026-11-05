import { useCallback, useEffect, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { AuthContext } from "./context";
import { request, api } from "../../lib/api/client";
import { session } from "../../lib/auth/session";
import type { Schema, User } from "../../types/models";

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [expired, setExpired] = useState(false);
  const [deadline, setDeadline] = useState<number | null>(null);
  const cache = useQueryClient();
  const clear = useCallback(() => {
    session.set(null);
    void cache.cancelQueries();
    cache.clear();
    setUser(null);
    setDeadline(null);
  }, [cache]);
  useEffect(
    () =>
      session.subscribe(() => {
        clear();
        setExpired(true);
      }),
    [clear],
  );
  useEffect(() => {
    if (!deadline) return;
    const timer = setTimeout(
      () => session.expire(),
      Math.max(0, deadline - Date.now()),
    );
    return () => clearTimeout(timer);
  }, [deadline]);
  async function login(email: string, password: string) {
    clear();
    setExpired(false);
    const token = await request<Schema<"TokenRead">>("/auth/token", {
      method: "POST",
      form: new URLSearchParams({ username: email, password }),
    });
    session.set(token.access_token);
    try {
      const current = await api.get<User>("/auth/me");
      setUser(current);
      setDeadline(Date.now() + token.expires_in * 1000);
    } catch (error) {
      clear();
      throw error;
    }
  }
  return (
    <AuthContext.Provider
      value={{
        user,
        expired,
        login,
        logout: () => {
          clear();
          setExpired(false);
        },
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}
