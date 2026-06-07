// Auth context: session tracking + token accessor.
// Wraps the Supabase session and exposes getToken() for injecting Bearer tokens
// into API calls and WebSocket URLs.

import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import type { Session } from "@supabase/supabase-js";
import { supabase } from "./supabaseClient";

interface AuthContextValue {
  session: Session | null;
  loading: boolean;
  /** Returns the current JWT access token, or null if not authenticated. */
  getToken: () => string | null;
  signInWithEmail: (email: string, password: string) => Promise<{ error: string | null }>;
  /** Create an account. `needsConfirmation` = true when Supabase requires email confirm. */
  signUpWithEmail: (
    email: string,
    password: string
  ) => Promise<{ error: string | null; needsConfirmation: boolean }>;
  /** Redirect to Google OAuth; resolves only with an error (success navigates away). */
  signInWithGoogle: () => Promise<{ error: string | null }>;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);

  // Mirror the session into a ref so `getToken` can be a STABLE closure that
  // always reads the latest token. Without this, getToken closes over the
  // `session` from the render it was created in; React runs child effects before
  // parent effects, so a consumer's mount-time request (e.g. createSession) can
  // fire before LoginGate re-wires the token provider — reading a stale closure
  // that still sees `session === null` and sending no Authorization header (401).
  const sessionRef = useRef<Session | null>(null);
  const applySession = useCallback((s: Session | null) => {
    sessionRef.current = s;
    setSession(s);
  }, []);

  useEffect(() => {
    // Hydrate from existing session (e.g. after page refresh).
    supabase.auth.getSession().then(({ data }) => {
      applySession(data.session);
      setLoading(false);
    });

    // Keep in sync with sign-in / sign-out events.
    const { data: listener } = supabase.auth.onAuthStateChange((_event, s) => {
      applySession(s);
    });

    return () => listener.subscription.unsubscribe();
  }, [applySession]);

  // Stable across renders: reads the ref, never a stale captured `session`.
  const getToken = useCallback((): string | null => sessionRef.current?.access_token ?? null, []);

  const signInWithEmail = async (
    email: string,
    password: string
  ): Promise<{ error: string | null }> => {
    const { error } = await supabase.auth.signInWithPassword({ email, password });
    return { error: error?.message ?? null };
  };

  const signUpWithEmail = async (
    email: string,
    password: string
  ): Promise<{ error: string | null; needsConfirmation: boolean }> => {
    const { data, error } = await supabase.auth.signUp({
      email,
      password,
      options: { emailRedirectTo: window.location.origin },
    });
    if (error) return { error: error.message, needsConfirmation: false };
    // If the project requires email confirmation, signUp returns a user but no
    // session — the caller should prompt the user to check their inbox.
    return { error: null, needsConfirmation: !data.session };
  };

  const signInWithGoogle = async (): Promise<{ error: string | null }> => {
    const { error } = await supabase.auth.signInWithOAuth({
      provider: "google",
      options: { redirectTo: window.location.origin },
    });
    // On success the browser navigates to Google; we only return on error.
    return { error: error?.message ?? null };
  };

  const signOut = async () => {
    await supabase.auth.signOut();
  };

  return (
    <AuthContext.Provider
      value={{
        session,
        loading,
        getToken,
        signInWithEmail,
        signUpWithEmail,
        signInWithGoogle,
        signOut,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
