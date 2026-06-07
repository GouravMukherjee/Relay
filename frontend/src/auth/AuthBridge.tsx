// Bridges the (lazy-loaded) auth session into the always-loaded Dashboard via a
// render-prop, so the Dashboard/TopNav never statically import Supabase. Only
// mounted on the functional path, inside <AuthProvider>.

import type { ReactNode } from "react";
import { useAuth } from "./AuthContext";

export interface Account {
  email: string | null;
  onSignOut: () => void | Promise<void>;
}

export function AuthBridge({ children }: { children: (account: Account) => ReactNode }) {
  const { session, signOut } = useAuth();
  return <>{children({ email: session?.user?.email ?? null, onSignOut: signOut })}</>;
}
