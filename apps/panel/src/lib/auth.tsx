import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

type AuthState = {
  token: null;
  authenticated: boolean;
  loading: boolean;
  markAuthenticated: () => void;
  logout: () => Promise<void>;
};

const AuthContext = createContext<AuthState | null>(null);
const CSRF_COOKIE = "site_panel_csrf";

function csrfToken(): string | undefined {
  return document.cookie
    .split("; ")
    .find((item) => item.startsWith(`${CSRF_COOKIE}=`))
    ?.split("=", 2)[1];
}

function isMutation(method?: string): boolean {
  return !["GET", "HEAD", "OPTIONS"].includes((method || "GET").toUpperCase());
}

async function refreshSession(): Promise<boolean> {
  const headers = new Headers();
  const csrf = csrfToken();
  if (csrf) headers.set("X-CSRF-Token", csrf);
  const res = await fetch("/api/v1/auth/refresh", {
    method: "POST",
    headers,
    credentials: "include",
  });
  return res.ok;
}

async function request(path: string, init: RequestInit, retryAfterRefresh: boolean): Promise<Response> {
  const headers = new Headers(init.headers);
  if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (isMutation(init.method)) {
    const csrf = csrfToken();
    if (csrf) headers.set("X-CSRF-Token", csrf);
  }

  const res = await fetch(path, { ...init, headers, credentials: "include" });
  if (res.status !== 401 || !retryAfterRefresh || path.startsWith("/api/v1/auth/")) return res;
  return (await refreshSession()) ? request(path, init, false) : res;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [authenticated, setAuthenticated] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api("/api/v1/security/me")
      .then(() => setAuthenticated(true))
      .catch(() => setAuthenticated(false))
      .finally(() => setLoading(false));
  }, []);

  const value = useMemo<AuthState>(
    () => ({
      token: null,
      authenticated,
      loading,
      markAuthenticated: () => setAuthenticated(true),
      logout: async () => {
        try {
          await api("/api/v1/auth/revoke", { method: "POST" });
        } finally {
          setAuthenticated(false);
        }
      },
    }),
    [authenticated, loading],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}

export async function download(path: string, init: RequestInit = {}, token?: string | null): Promise<Blob> {
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const res = await request(path, { ...init, headers }, true);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || res.statusText);
  }
  return res.blob();
}

export async function api<T>(path: string, init: RequestInit = {}, token?: string | null): Promise<T> {
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const res = await request(path, { ...init, headers }, true);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || res.statusText);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}
