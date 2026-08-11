/**
 * Session state for the Workbench.
 *
 * The client's view of who is signed in is a **convenience, not a control**. Every guard here
 * has a counterpart in `engine/api/auth.py`, and the server's is the one that matters —
 * hiding a button an unauthorised caller could still reach by URL would be theatre. What this
 * buys is that an operator sees what they can act on rather than a wall of 403s.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { ApiError, api } from "./api";
import type { Principal, Role } from "./types";

interface Session {
  principal: Principal | null;
  /** False until the first `whoami` settles, so the shell does not flash a sign-in page. */
  ready: boolean;
  /** Whether any account exists at all. A fresh deployment needs a different message. */
  configured: boolean;
  signIn: (email: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
}

const SessionContext = createContext<Session | null>(null);

const RANK: Record<Role, number> = { viewer: 0, approver: 1, admin: 2 };

export function SessionProvider({ children }: { children: ReactNode }) {
  const [principal, setPrincipal] = useState<Principal | null>(null);
  const [ready, setReady] = useState(false);
  const [configured, setConfigured] = useState(true);

  const refresh = useCallback(async () => {
    try {
      setPrincipal(await api.whoami());
    } catch (e) {
      // A 401 is the expected answer for "not signed in", not a failure worth surfacing.
      if (!(e instanceof ApiError) || e.status !== 401) console.error(e);
      setPrincipal(null);
      try {
        setConfigured((await api.bootstrapState()).configured);
      } catch {
        setConfigured(true);
      }
    } finally {
      setReady(true);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Any 401 from any call, not just the one at mount. Without this the app keeps rendering a
  // signed-in shell whose every request is being refused, and the user debugs the feature
  // they happened to click rather than their session.
  useEffect(() => {
    const onSignedOut = () => setPrincipal(null);
    window.addEventListener("kuwarden:signed-out", onSignedOut);
    return () => window.removeEventListener("kuwarden:signed-out", onSignedOut);
  }, []);

  const value: Session = {
    principal,
    ready,
    configured,
    async signIn(email, password) {
      setPrincipal(await api.signIn(email, password));
    },
    async signOut() {
      await api.signOut();
      setPrincipal(null);
    },
  };

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): Session {
  const session = useContext(SessionContext);
  if (!session) throw new Error("useSession must be used inside a SessionProvider");
  return session;
}

/**
 * Whether the signed-in user holds at least `role`.
 *
 * Mirrors `Principal.can` on the server. Use it to hide controls, never to decide whether an
 * action is safe — the server decides that.
 */
export function useCan(role: Role): boolean {
  const { principal } = useSession();
  return principal !== null && RANK[principal.role] >= RANK[role];
}
