import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { httpClient } from '@/api/httpClient';
import type { GoogleFinishInput, GoogleSessionResult, RegisterInput, SessionUser } from '@/api/types';

// App-wide auth state, backed by the ApiClient. `ready` is false until the persisted token
// pair has been loaded/validated on startup, so the router can avoid flashing the wrong
// screen before we know whether there's a session.
interface AuthState {
  ready: boolean;
  user: SessionUser | null;
  login: (userid: string, password: string) => Promise<void>;
  register: (input: RegisterInput) => Promise<void>;
  logout: () => Promise<void>;
  // Resolve a Google handoff token; sets the user if it's a full session, otherwise reports
  // a pending new account for the caller to complete via googleFinish.
  googleSession: (handoff: string) => Promise<GoogleSessionResult>;
  googleFinish: (handoff: string, consent: GoogleFinishInput) => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(false);
  const [user, setUser] = useState<SessionUser | null>(null);

  useEffect(() => {
    let alive = true;
    httpClient
      .initAuth()
      .then((u) => {
        if (alive) setUser(u);
      })
      .catch(() => {
        if (alive) setUser(null);
      })
      .finally(() => {
        if (alive) setReady(true);
      });
    return () => {
      alive = false;
    };
  }, []);

  const value = useMemo<AuthState>(
    () => ({
      ready,
      user,
      async login(userid, password) {
        setUser(await httpClient.login(userid, password));
      },
      async register(input) {
        setUser(await httpClient.register(input));
      },
      async logout() {
        await httpClient.logout();
        setUser(null);
      },
      async googleSession(handoff) {
        const result = await httpClient.googleSession(handoff);
        if (result.status === 'session') setUser(result.user);
        return result;
      },
      async googleFinish(handoff, consent) {
        setUser(await httpClient.googleFinish(handoff, consent));
      },
    }),
    [ready, user],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within <AuthProvider>');
  return ctx;
}
