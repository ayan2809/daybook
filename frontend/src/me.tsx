// Who is signed in (GET /api/me), shared through context.
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, errorMessage, setUnauthorizedHandler } from "./api";
import type { Me, Settings } from "./types";

interface MeState {
  me: Me | null;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<Me | null>;
  setSettings: (s: Settings) => void;
}

const MeContext = createContext<MeState | null>(null);

const SIGNED_OUT: Me = {
  role: null,
  settings: null,
  demo: null,
  features: { voice: false, push: false, vapid_public_key: null },
};

export function MeProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const m = await api.me();
      setMe(m);
      setError(null);
      return m;
    } catch (err) {
      setError(errorMessage(err));
      return null;
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    // Warm the backend so the first real request is fast (plan §11). Failures don't matter.
    api.health(true).catch(() => undefined);
  }, [refresh]);

  useEffect(() => {
    setUnauthorizedHandler(() => setMe((prev) => (prev?.role ? { ...SIGNED_OUT, features: prev.features } : prev)));
    return () => setUnauthorizedHandler(null);
  }, []);

  const setSettings = useCallback((s: Settings) => setMe((prev) => (prev ? { ...prev, settings: s } : prev)), []);

  const value = useMemo(
    () => ({ me, loading, error, refresh, setSettings }),
    [me, loading, error, refresh, setSettings],
  );
  return <MeContext.Provider value={value}>{children}</MeContext.Provider>;
}

export function useMe(): MeState {
  const ctx = useContext(MeContext);
  if (!ctx) throw new Error("useMe outside MeProvider");
  return ctx;
}
