import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api } from "../api/client.js";

const ConfigContext = createContext(null);

/**
 * Server-provided configuration: the seven input definitions with their
 * limits, and which optional features (weather lookup, password reset) are
 * switched on. Loaded once; pages that do not need it are never blocked.
 */
export function ConfigProvider({ children }) {
  const [state, setState] = useState({ status: "loading", config: null, error: null });

  const load = useCallback(async () => {
    setState((current) => ({ ...current, status: "loading", error: null }));
    try {
      setState({ status: "ready", config: await api.config(), error: null });
    } catch (error) {
      setState({ status: "error", config: null, error });
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const value = useMemo(() => ({ ...state, reload: load }), [state, load]);
  return <ConfigContext.Provider value={value}>{children}</ConfigContext.Provider>;
}

export function useConfig() {
  const context = useContext(ConfigContext);
  if (!context) {
    throw new Error("useConfig must be used inside <ConfigProvider>");
  }
  return context;
}
