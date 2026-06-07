// Renders the login form when the user is not authenticated.
// Passes children through once a session exists.
// Only mounted when VITE_USE_MOCK=false.

import { useEffect, useState, type ReactNode } from "react";
import { useAuth } from "./AuthContext";
import { setTokenProvider } from "../api/client";
import { setWsTokenProvider } from "../hooks/useRelaySession";

export function LoginGate({ children }: { children: ReactNode }) {
  const auth = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Wire token providers once we have a stable getToken reference.
  useEffect(() => {
    setTokenProvider(auth.getToken);
    setWsTokenProvider(auth.getToken);
  }, [auth.getToken]);

  if (auth.loading) {
    return (
      <div className="login-loading">
        <span>Loading…</span>
      </div>
    );
  }

  if (auth.session) return <>{children}</>;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const { error: err } = await auth.signInWithEmail(email, password);
    setBusy(false);
    if (err) setError(err);
  };

  return (
    <div className="login-root">
      <div className="login-card">
        <div className="login-brand">
          <span className="login-brand-name">Relay</span>
          <span className="login-brand-tagline">Ambient AI co-pilot</span>
        </div>
        <form className="login-form" onSubmit={handleSubmit}>
          <label className="login-label">
            Email
            <input
              className="login-input"
              type="email"
              autoComplete="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </label>
          <label className="login-label">
            Password
            <input
              className="login-input"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>
          {error && <p className="login-error">{error}</p>}
          <button className="login-submit" type="submit" disabled={busy}>
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </form>
      </div>
    </div>
  );
}
