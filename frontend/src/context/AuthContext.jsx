import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api, resetCsrfToken, setUnauthorizedHandler } from "../api/client.js";

const AuthContext = createContext(null);

/**
 * Holds who is signed in. The source of truth is the server-side session;
 * this only mirrors it, so nothing sensitive is kept in browser storage.
 *
 * status: "loading" (asking the server), "ready", or "error" (server unreachable)
 */
export function AuthProvider({ children }) {
  const [state, setState] = useState({ status: "loading", user: null, error: null });

  const refresh = useCallback(async () => {
    try {
      const data = await api.me();
      setState({ status: "ready", user: data.authenticated ? data.user : null, error: null });
    } catch (error) {
      setState({ status: "error", user: null, error });
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    // Any API call that finds the session gone signs the UI out too.
    setUnauthorizedHandler(() => {
      resetCsrfToken();
      setState((current) => (current.user ? { status: "ready", user: null, error: null } : current));
    });
    return () => setUnauthorizedHandler(null);
  }, []);

  const login = useCallback(async (email, password) => {
    const data = await api.login({ email, password });
    resetCsrfToken(); // the server started a new session
    setState({ status: "ready", user: data.user, error: null });
    return data.user;
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } finally {
      resetCsrfToken();
      setState({ status: "ready", user: null, error: null });
    }
  }, []);

  const value = useMemo(() => ({ ...state, login, logout, refresh }), [state, login, logout, refresh]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used inside <AuthProvider>");
  }
  return context;
}
