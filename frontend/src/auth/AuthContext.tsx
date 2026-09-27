import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { httpClient } from '@/api/httpClient';
import { syncTrackerFromCatalog, verifyStaleDeadlines } from '@/api/trackerStore';
import { identify, setTag } from '@/lib/analytics';
import type { AllowanceSnapshot, AppleCredential, AppleSessionResult, GoogleFinishInput, GoogleSessionResult, RegisterInput, SessionUser } from '@/api/types';

// App-wide auth state, backed by the ApiClient. `ready` is false until the persisted token
// pair has been loaded/validated on startup, so the router can avoid flashing the wrong
// screen before we know whether there's a session.
interface AuthState {
  ready: boolean;
  user: SessionUser | null;
  // The Free-tier daily AI allowance snapshot (two-tier model), or null until one is known.
  // Broadcast by the ApiClient from 429s, /api/ai meta.allowance, and subscriptionStatus().
  allowance: AllowanceSnapshot | null;
  login: (userid: string, password: string) => Promise<void>;
  register: (input: RegisterInput) => Promise<void>;
  logout: () => Promise<void>;
  // Resolve a Google handoff token; sets the user if it's a full session, otherwise reports
  // a pending new account for the caller to complete via googleFinish.
  googleSession: (handoff: string) => Promise<GoogleSessionResult>;
  googleFinish: (handoff: string, consent: GoogleFinishInput) => Promise<void>;
  // Sign in with Apple (native iOS). appleSignIn resolves the identity token — a full session
  // (user is set) or a pending new account for the caller to complete via appleFinish.
  appleSignIn: (cred: AppleCredential) => Promise<AppleSessionResult>;
  appleFinish: (cred: AppleCredential, consent: GoogleFinishInput) => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(false);
  const [user, setUser] = useState<SessionUser | null>(null);
  const [allowance, setAllowance] = useState<AllowanceSnapshot | null>(null);

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

  // initAuth can now return a session restored from cache and revalidate it in the
  // background, so "this session is dead" can arrive AFTER the first render. Clearing the
  // user here is what makes (app)/_layout redirect to /login when that happens.
  useEffect(() => httpClient.onSessionLost(() => setUser(null)), []);

  // The identity can also change WITHOUT the session ending: the background refresh (and
  // every 402 from the subscription gate) carries a fresh has_access. Mirroring it into
  // state is what makes (app)/_layout send an account whose trial lapsed mid-session to
  // the paywall, instead of leaving it on a screen whose every request now fails.
  useEffect(() => httpClient.onUserChanged((u) => setUser(u)), []);

  // The Free-tier allowance meter, broadcast the same way: a 429 cap hit, a successful AI
  // call's echoed snapshot, or a subscriptionStatus() read. Screens read it via useAuth().
  useEffect(() => httpClient.onAllowanceChanged((a) => setAllowance(a)), []);

  // App-open / login: force a free catalog sync so already-tracked items pick up whatever
  // changed while the app was closed (an agent run, another student's on-demand check), then
  // fan out a fresh deadline check for any tracked row that is missing dates or has gone stale
  // (>7 days) — so the student never opens Home Base / Quest Log to data older than the
  // staleness window. Keyed on userid, NOT the user object, so it fires once per genuine
  // login/restore and NOT on every background token refresh or 402 (those keep the same userid).
  //
  // MARQUEE M9: verifyStaleDeadlines makes paid Claude calls (approved 2026-09-27, all users,
  // login + Quest Log focus). It is bounded: force=false so the server's 7-day gate + cross-user
  // cache serve any row re-checked elsewhere this week for free; per-id-per-session dedupe
  // (_freshCheckAttempted, shared with the Quest Log focus pass) so a row is attempted at most
  // once per session and the two triggers never double-bill; the paid-lane semaphore and the
  // Free-tier budget/allowance still gate each call. Never throws and no-ops for a signed-out/
  // lapsed account, so it is safe to fire-and-forget here (no card handlers — this only warms
  // the local + cross-user cache; the screens render the fresh data on their next focus load).
  useEffect(() => {
    if (!user?.userid) return;
    void syncTrackerFromCatalog({ force: true })
      .then((r) => {
        if (r.needsCheck.length) return verifyStaleDeadlines(r.needsCheck);
      })
      .catch(() => null);
  }, [user?.userid]);

  // Clarity: tie the (web) session to the opaque account id once per genuine login/restore.
  // Keyed on userid so it does not re-fire on background refreshes. No PII — id only.
  useEffect(() => {
    if (!user?.userid) return;
    identify(user.userid);
  }, [user?.userid]);

  // Clarity plan segmentation. Re-runs when the tier changes (upgrade/cancel/lapse) since it
  // reads the subscription block, which the background refresh and 402 gate keep fresh.
  useEffect(() => {
    if (!user?.userid) return;
    setTag('plan', user.subscription?.status || user.subscription?.ai_tier || 'unknown');
  }, [user?.userid, user?.subscription?.status, user?.subscription?.ai_tier]);

  const value = useMemo<AuthState>(
    () => ({
      ready,
      user,
      allowance,
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
      async appleSignIn(cred) {
        const result = await httpClient.appleNative(cred);
        if (result.status === 'session') setUser(result.user);
        return result;
      },
      async appleFinish(cred, consent) {
        const result = await httpClient.appleNative(cred, consent);
        // With consent the backend creates (or logs into) the account and returns a session;
        // a lingering `pending` here would mean the consent was not accepted server-side.
        if (result.status !== 'session') {
          throw new Error('Apple sign-in could not be completed. Please try again.');
        }
        setUser(result.user);
      },
    }),
    [ready, user, allowance],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within <AuthProvider>');
  return ctx;
}
