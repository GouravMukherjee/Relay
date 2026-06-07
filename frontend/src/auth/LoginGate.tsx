// Renders the auth screen when the user is not authenticated: Google OAuth plus
// email/password sign-in and account creation. Passes children through once a
// session exists.

import { useEffect, useState, type ReactNode } from "react";
import { useAuth } from "./AuthContext";
import { setTokenProvider } from "../api/client";
import { setWsTokenProvider } from "../hooks/useRelaySession";

type Mode = "signin" | "signup";

export function LoginGate({ children }: { children: ReactNode }) {
  const auth = useAuth();
  const [mode, setMode] = useState<Mode>("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
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

  const handleGoogle = async () => {
    setBusy(true);
    setError(null);
    setNotice(null);
    const { error: err } = await auth.signInWithGoogle();
    if (err) {
      setError(err);
      setBusy(false);
    }
    // On success the browser redirects to Google; no need to clear busy.
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setNotice(null);

    if (mode === "signin") {
      const { error: err } = await auth.signInWithEmail(email, password);
      setBusy(false);
      if (err) setError(err);
    } else {
      const { error: err, needsConfirmation } = await auth.signUpWithEmail(email, password);
      setBusy(false);
      if (err) {
        setError(err);
      } else if (needsConfirmation) {
        setNotice("Account created — check your email to confirm, then sign in.");
        setMode("signin");
      }
      // If no confirmation is required, onAuthStateChange flips us into the app.
    }
  };

  const switchMode = (next: Mode) => {
    setMode(next);
    setError(null);
    setNotice(null);
  };

  return (
    <div className="login-root">
      <div className="login-card">
        <div className="login-brand">
          <span className="login-brand-name">Relay</span>
          <span className="login-brand-tagline">Ambient AI co-pilot</span>
        </div>

        <button
          type="button"
          className="login-google"
          onClick={handleGoogle}
          disabled={busy}
        >
          <svg className="login-google-icon" viewBox="0 0 18 18" aria-hidden="true">
            <path fill="#4285F4" d="M17.64 9.2c0-.64-.06-1.25-.16-1.84H9v3.48h4.84a4.14 4.14 0 0 1-1.8 2.72v2.26h2.92c1.71-1.57 2.68-3.89 2.68-6.62z" />
            <path fill="#34A853" d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.92-2.26c-.81.54-1.84.86-3.04.86-2.34 0-4.32-1.58-5.03-3.7H.96v2.33A9 9 0 0 0 9 18z" />
            <path fill="#FBBC05" d="M3.97 10.72a5.4 5.4 0 0 1 0-3.44V4.95H.96a9 9 0 0 0 0 8.1l3.01-2.33z" />
            <path fill="#EA4335" d="M9 3.58c1.32 0 2.5.45 3.44 1.35l2.58-2.58C13.46.89 11.42 0 9 0A9 9 0 0 0 .96 4.95l3.01 2.33C4.68 5.16 6.66 3.58 9 3.58z" />
          </svg>
          Continue with Google
        </button>

        <div className="login-divider"><span>or</span></div>

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
              autoComplete={mode === "signin" ? "current-password" : "new-password"}
              required
              minLength={6}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>
          {error && <p className="login-error">{error}</p>}
          {notice && <p className="login-notice">{notice}</p>}
          <button className="login-submit" type="submit" disabled={busy}>
            {busy
              ? mode === "signin"
                ? "Signing in…"
                : "Creating account…"
              : mode === "signin"
                ? "Sign in"
                : "Create account"}
          </button>
        </form>

        <p className="login-switch">
          {mode === "signin" ? (
            <>
              No account?{" "}
              <button type="button" className="login-link" onClick={() => switchMode("signup")}>
                Create one
              </button>
            </>
          ) : (
            <>
              Already have an account?{" "}
              <button type="button" className="login-link" onClick={() => switchMode("signin")}>
                Sign in
              </button>
            </>
          )}
        </p>
      </div>
    </div>
  );
}
