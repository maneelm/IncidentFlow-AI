import { createContext, useContext } from "react";
import type { User } from "../../types/models";
export interface AuthState {
  user: User | null;
  expired: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
}
export const AuthContext = createContext<AuthState | null>(null);
export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error("AuthProvider required");
  return value;
}
