// Firebase client init (specs/02, specs/12). The web client is READ-ONLY against
// Firestore read models; all mutations go through the Django command API. Init is
// lazy and guarded so `next build` (and unit tests) succeed without real env vars
// or a live backend. Emulator wiring is driven by NEXT_PUBLIC_USE_FIREBASE_EMULATOR
// (specs/21 §21.3).

import { type FirebaseApp, getApp, getApps, initializeApp } from "firebase/app";
import { type Auth, connectAuthEmulator, getAuth } from "firebase/auth";
import {
  type Firestore,
  connectFirestoreEmulator,
  getFirestore,
} from "firebase/firestore";

const firebaseConfig = {
  apiKey: process.env.NEXT_PUBLIC_FIREBASE_API_KEY,
  authDomain: process.env.NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN,
  projectId: process.env.NEXT_PUBLIC_FIREBASE_PROJECT_ID,
  storageBucket: process.env.NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET,
  messagingSenderId: process.env.NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID,
  appId: process.env.NEXT_PUBLIC_FIREBASE_APP_ID,
};

const useEmulator = process.env.NEXT_PUBLIC_USE_FIREBASE_EMULATOR === "true";

/**
 * True when the client is wired to the local Firebase emulators (dev/CI) rather than a live
 * deployment — driven by NEXT_PUBLIC_USE_FIREBASE_EMULATOR (specs/21 §21.3). Exposed so the UI
 * can label the environment accurately (emulator "not production auth" vs a live demo deploy).
 */
export const usingEmulator = useEmulator;

let cachedApp: FirebaseApp | null = null;
let cachedAuth: Auth | null = null;
let cachedDb: Firestore | null = null;
let emulatorsConnected = false;

/** Lazily initialize (or reuse) the Firebase app. Safe to call during SSR/build. */
export function getFirebaseApp(): FirebaseApp {
  if (cachedApp) return cachedApp;
  // For emulator/demo the projectId is enough; a placeholder apiKey keeps init from
  // throwing when real web config isn't present (e.g. CI build).
  cachedApp = getApps().length
    ? getApp()
    : initializeApp({
        ...firebaseConfig,
        apiKey: firebaseConfig.apiKey ?? "demo-api-key",
        projectId: firebaseConfig.projectId ?? "demo-benefitservicing-workbench",
      });
  return cachedApp;
}

function connectEmulatorsOnce(auth: Auth, db: Firestore): void {
  if (emulatorsConnected || !useEmulator || typeof window === "undefined") return;
  emulatorsConnected = true;

  const authHost =
    process.env.NEXT_PUBLIC_FIREBASE_AUTH_EMULATOR_HOST ?? "http://localhost:9099";
  connectAuthEmulator(auth, authHost, { disableWarnings: true });

  const firestoreHost =
    process.env.NEXT_PUBLIC_FIRESTORE_EMULATOR_HOST ?? "localhost:8080";
  const [host, portStr] = firestoreHost.split(":");
  connectFirestoreEmulator(db, host || "localhost", Number(portStr ?? 8080));
}

/** Firebase Auth handle (lazy). */
export function getFirebaseAuth(): Auth {
  if (cachedAuth) return cachedAuth;
  const app = getFirebaseApp();
  cachedAuth = getAuth(app);
  connectEmulatorsOnce(cachedAuth, getFirebaseDb());
  return cachedAuth;
}

/** Firestore handle (lazy). Read-only usage from the client. */
export function getFirebaseDb(): Firestore {
  if (cachedDb) return cachedDb;
  const app = getFirebaseApp();
  cachedDb = getFirestore(app);
  if (useEmulator && typeof window !== "undefined") {
    // Ensure emulator is wired even if getFirebaseDb() is reached before getFirebaseAuth().
    connectEmulatorsOnce(getAuth(app), cachedDb);
  }
  return cachedDb;
}
