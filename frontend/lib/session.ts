"use client";

// session.ts — the minimal EMULATOR sign-in + live session surface (specs/12,
// specs/18 §18.1). This is DEMO auth wiring, not production identity: it wraps the
// shared Firebase Auth handle (lib/firebase.ts, already emulator-wired) so the app
// can sign in against the seeded demo accounts and observe the live session.
//
// It is deliberately a SINGLE module-level store fed by ONE `onIdTokenChanged`
// listener, surfaced through `useSyncExternalStore`. That matters for correctness:
// the "was this a deliberate sign-out or did the session EXPIRE?" decision must be
// global. If every `useSession()` caller kept its own listener + "was authed" flag,
// a deliberate sign-out from the TopBar would leave the SessionBanner's independent
// copy still thinking the session had expired — and it would wrongly show the
// "session expired" banner. One shared store makes that decision once.
//
// Role is READ from the Firebase custom claim (`getIdTokenResult().claims.role`) —
// the authoritative source for both Firestore rules and Django (specs/12). The UI
// uses it for affordance only; the server still authorizes every write.

import { useSyncExternalStore } from "react";
import { FirebaseError } from "firebase/app";
import {
  onIdTokenChanged,
  signInWithEmailAndPassword,
  signOut as firebaseSignOut,
  type User,
} from "firebase/auth";
import { getFirebaseAuth } from "@/lib/firebase";
import { ROLES, type Role } from "@/lib/types";

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

/**
 * - `loading`   — the initial auth state has not resolved yet (don't decide anything).
 * - `authed`    — a signed-in user with a valid token (role resolved from the claim).
 * - `anonymous` — no user, and none was ever signed in this session (or a deliberate
 *                 sign-out) — the ordinary logged-out state.
 * - `expired`   — a PREVIOUSLY-authed session dropped to a null token WITHOUT a
 *                 deliberate sign-out (revoked/disabled/refresh failure). Drives the
 *                 "session expired, sign in again" banner.
 */
export type SessionStatus = "loading" | "authed" | "anonymous" | "expired";

export interface Session {
  user: User | null;
  /** From the Firebase custom claim; null when unknown / no role claim. */
  role: Role | null;
  status: SessionStatus;
  /** Deliberate sign-out — clears the token and lands on `anonymous` (never `expired`). */
  signOut: () => Promise<void>;
}

/** Result of an emulator sign-in attempt — never throws; failure is surfaced here. */
export type SignInResult = { ok: true } | { ok: false; message: string };

// ---------------------------------------------------------------------------
// Module-level store (one listener, shared by every useSession() caller)
// ---------------------------------------------------------------------------

interface SessionState {
  user: User | null;
  role: Role | null;
  status: SessionStatus;
}

const INITIAL_STATE: SessionState = { user: null, role: null, status: "loading" };
// Stable reference used for SSR/first-hydration render (must not change identity).
const SERVER_SNAPSHOT: SessionState = INITIAL_STATE;

let state: SessionState = INITIAL_STATE;
/** True once any user has been observed — lets us tell `expired` from initial `anonymous`. */
let wasAuthed = false;
/** Set by signOut() so the ensuing null-token callback resolves to `anonymous`, not `expired`. */
let intentionalSignOut = false;
/** Guards against installing the auth listener more than once. */
let listenerStarted = false;
/**
 * Monotonic token-change sequence. An in-flight `getIdTokenResult()` only publishes if
 * it is still the latest change — a newer sign-out/switch supersedes a stale resolve.
 */
let changeSeq = 0;

const listeners = new Set<() => void>();

function emit(): void {
  for (const listener of listeners) listener();
}

function setState(next: SessionState): void {
  state = next;
  emit();
}

function isRole(value: unknown): value is Role {
  return typeof value === "string" && (ROLES as readonly string[]).includes(value);
}

function handleToken(user: User | null): void {
  const seq = ++changeSeq;

  if (user) {
    wasAuthed = true;
    intentionalSignOut = false;
    // Resolve the role from the claim BEFORE flipping to `authed`, so the transition is
    // atomic (user + role together) and RoleGate affordances never flash with a null role.
    // Fire-and-forget: both outcomes are handled below, so there is no unhandled rejection.
    void user.getIdTokenResult().then(
      (result) => {
        if (seq !== changeSeq) return; // superseded by a newer auth change
        setState({
          user,
          role: isRole(result.claims.role) ? result.claims.role : null,
          status: "authed",
        });
      },
      () => {
        if (seq !== changeSeq) return;
        setState({ user, role: null, status: "authed" });
      },
    );
    return;
  }

  // No user. A deliberate sign-out → anonymous; an unexpected drop after having been
  // authed → expired; otherwise (fresh load, never signed in) → anonymous.
  const status: SessionStatus = intentionalSignOut
    ? "anonymous"
    : wasAuthed
      ? "expired"
      : "anonymous";
  intentionalSignOut = false;
  setState({ user: null, role: null, status });
}

function startListenerOnce(): void {
  if (listenerStarted || typeof window === "undefined") return;
  listenerStarted = true;
  // Never torn down: this is an app-lifetime singleton listener.
  onIdTokenChanged(getFirebaseAuth(), handleToken);
}

function subscribe(onStoreChange: () => void): () => void {
  startListenerOnce();
  listeners.add(onStoreChange);
  return () => {
    listeners.delete(onStoreChange);
  };
}

function getSnapshot(): SessionState {
  return state;
}

function getServerSnapshot(): SessionState {
  return SERVER_SNAPSHOT;
}

async function performSignOut(): Promise<void> {
  // Mark the intent BEFORE clearing the token so the null-token callback reads it and
  // resolves to `anonymous` rather than `expired`.
  intentionalSignOut = true;
  wasAuthed = false;
  try {
    await firebaseSignOut(getFirebaseAuth());
  } catch {
    // If sign-out failed the token never cleared; clear the intent so a genuine later
    // expiry is still reported as `expired`.
    intentionalSignOut = false;
  }
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Sign in against the local Auth emulator with a seeded demo account
 * (backend/seed/users.py). Never throws — a failure is returned as
 * `{ ok: false, message }` with operator-facing copy for the sign-in page.
 */
export async function signInWithEmulator(
  email: string,
  password: string,
): Promise<SignInResult> {
  try {
    await signInWithEmailAndPassword(getFirebaseAuth(), email.trim(), password);
    return { ok: true };
  } catch (error) {
    return { ok: false, message: signInErrorMessage(error) };
  }
}

function signInErrorMessage(error: unknown): string {
  if (error instanceof FirebaseError) {
    switch (error.code) {
      case "auth/invalid-email":
        return "Enter a valid email address.";
      case "auth/missing-password":
        return "Enter your password.";
      case "auth/user-disabled":
        return "This account is disabled.";
      case "auth/invalid-credential":
      case "auth/wrong-password":
      case "auth/user-not-found":
        return "Email or password is incorrect.";
      case "auth/too-many-requests":
        return "Too many attempts. Wait a moment and try again.";
      case "auth/network-request-failed":
        return "Couldn't reach the sign-in service. Is the Auth emulator running?";
      default:
        return "Couldn't sign in. Confirm the Auth emulator is running and try again.";
    }
  }
  return "Couldn't sign in. Please try again.";
}

/** Live session: `{ user, role, status, signOut }`. Reads the shared auth store. */
export function useSession(): Session {
  const snapshot = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
  return {
    user: snapshot.user,
    role: snapshot.role,
    status: snapshot.status,
    signOut: performSignOut,
  };
}
