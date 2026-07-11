/**
 * Firestore security-rules tests — specs/12-auth-and-security.md §12.6.
 *
 * Verifies the read-path guarantees (Django is NOT in the read path, so security rules
 * are the only read gate — specs/02 P7):
 *   - protected collections are UNREADABLE without a valid role claim
 *   - protected collections are UNWRITABLE by ANY client (writes go through the backend)
 *   - protected collections are READABLE with a valid servicing role claim
 *   - users/{uid} self-write is DENIED (no role self-escalation)
 *   - idempotencyKeys + simulatedCharges are fully client-invisible
 *
 * Run against the Firestore emulator (from the firebase/ directory):
 *   npm run test:rules:ci          # wraps: firebase emulators:exec --only firestore "vitest run"
 *   npm run test:rules             # if the emulator is already running
 *
 * NOTE: rules-unit-testing caches contexts per identity, and a context's `.firestore()`
 * may only be initialized once — so every identity's Firestore handle is created ONCE
 * in beforeAll and reused across tests (creating them per-test throws "Firestore has
 * already been started and its settings can no longer be changed").
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import {
  initializeTestEnvironment,
  assertFails,
  assertSucceeds,
  type RulesTestEnvironment,
  type RulesTestContext,
} from "@firebase/rules-unit-testing";
import { collection, doc, getDoc, getDocs, setDoc } from "firebase/firestore";
import { afterAll, beforeAll, beforeEach, describe, it } from "vitest";

const PROJECT_ID = "demo-benefitservicing-workbench";
const RULES_PATH = fileURLToPath(new URL("../firestore.rules", import.meta.url));

// Discover the emulator from the env var set by `firebase emulators:exec`; fall back to
// the port declared in firebase.json for a manually-started emulator.
const [emuHost, emuPort] = (process.env.FIRESTORE_EMULATOR_HOST ?? "127.0.0.1:8080").split(":");

// One representative document per protected collection (incl. subcollections & mirrors).
const OPERATIONAL_PATHS = [
  "employers/emp_1",
  "borrowers/bor_1",
  "borrowers/bor_1/events/evt_1", // borrower-scoped event mirror
  "loans/loan_1",
  "loans/loan_1/notes/note_1",
  "loans/loan_1/events/evt_1", // loan-scoped event mirror
  "benefitAgreements/ben_1",
  "scheduledContributions/ben_1__001",
  "scheduledContributions/ben_1__001/attempts/ben_1__001__att_001",
  "operationalExceptions/ex_1",
  "servicingEvents/evt_1",
];

const READ_MODEL_PATHS = [
  "portfolioSummaries/current",
  "portfolioSummaries/2026-07",
  "employerSummaries/emp_1",
  "employerSummaries/emp_1/periods/2026-07",
  "loanWorkbenches/loan_1",
];

const ALL_PROTECTED = [...OPERATIONAL_PATHS, ...READ_MODEL_PATHS];

const INVISIBLE_PATHS = ["idempotencyKeys/key_1", "simulatedCharges/pay_x_att_001"];

const SELF = "user_ops"; // ops identity's uid, for the users/{uid} self-read/self-write tests

type Db = ReturnType<RulesTestContext["firestore"]>;

let testEnv: RulesTestEnvironment;
// One Firestore handle per identity, initialized exactly once (see NOTE above).
let dbUnauth: Db;
let dbNoRole: Db;
let dbOps: Db;
let dbMgr: Db;
let dbAdmin: Db;

beforeAll(async () => {
  testEnv = await initializeTestEnvironment({
    projectId: PROJECT_ID,
    firestore: {
      rules: readFileSync(RULES_PATH, "utf8"),
      host: emuHost,
      port: Number(emuPort),
    },
  });
  dbUnauth = testEnv.unauthenticatedContext().firestore();
  dbNoRole = testEnv.authenticatedContext("user_norole", {}).firestore(); // signed in, no role claim
  dbOps = testEnv.authenticatedContext(SELF, { role: "OPERATIONS_USER" }).firestore();
  dbMgr = testEnv.authenticatedContext("user_mgr", { role: "SERVICING_MANAGER" }).firestore();
  dbAdmin = testEnv.authenticatedContext("user_admin", { role: "ADMINISTRATOR" }).firestore();
});

afterAll(async () => {
  await testEnv.cleanup();
});

beforeEach(async () => {
  await testEnv.clearFirestore();
});

/** Seed a document bypassing rules, so read-allow assertions read real data. */
async function seed(path: string, data: Record<string, unknown>): Promise<void> {
  await testEnv.withSecurityRulesDisabled(async (ctx) => {
    await setDoc(doc(ctx.firestore(), path), data);
  });
}

describe("unauthenticated users", () => {
  it("cannot read any protected collection", async () => {
    for (const path of ALL_PROTECTED) {
      await assertFails(getDoc(doc(dbUnauth, path)));
    }
  });

  it("cannot write any protected collection", async () => {
    for (const path of ALL_PROTECTED) {
      await assertFails(setDoc(doc(dbUnauth, path), { x: 1 }));
    }
  });
});

describe("authenticated users WITHOUT a role claim", () => {
  it("cannot read protected collections (isServicingUser() is false)", async () => {
    for (const path of ALL_PROTECTED) {
      await assertFails(getDoc(doc(dbNoRole, path)));
    }
  });
});

describe("servicing users (valid role claim)", () => {
  it("OPERATIONS_USER can read every operational collection and read model", async () => {
    await seed("loans/loan_1", { borrowerId: "bor_1", loanStatus: "ACTIVE" });
    for (const path of ALL_PROTECTED) {
      // getDoc on an allowed path resolves even when the doc doesn't exist — that proves the rule allows read.
      await assertSucceeds(getDoc(doc(dbOps, path)));
    }
  });

  it("can run a list/query read (read rules cover list)", async () => {
    await seed("loans/loan_1", { borrowerId: "bor_1", loanStatus: "ACTIVE" });
    await assertSucceeds(getDocs(collection(dbOps, "loans")));
  });

  it("CANNOT write any protected collection — even as ADMINISTRATOR", async () => {
    for (const path of ALL_PROTECTED) {
      await assertFails(setDoc(doc(dbAdmin, path), { x: 1 }));
    }
  });
});

describe("idempotencyKeys & simulatedCharges — fully client-invisible", () => {
  it("cannot be read or written by any client, including admin", async () => {
    for (const db of [dbUnauth, dbOps, dbMgr, dbAdmin]) {
      for (const path of INVISIBLE_PATHS) {
        await assertFails(getDoc(doc(db, path)));
        await assertFails(setDoc(doc(db, path), { x: 1 }));
      }
    }
  });
});

describe("users/{uid}", () => {
  it("a user can read their own doc", async () => {
    await seed(`users/${SELF}`, { role: "OPERATIONS_USER" });
    await assertSucceeds(getDoc(doc(dbOps, `users/${SELF}`)));
  });

  it("a non-admin cannot read another user's doc", async () => {
    await seed("users/user_other", { role: "OPERATIONS_USER" });
    await assertFails(getDoc(doc(dbOps, "users/user_other")));
  });

  it("an admin can read any user's doc", async () => {
    await seed("users/user_other", { role: "OPERATIONS_USER" });
    await assertSucceeds(getDoc(doc(dbAdmin, "users/user_other")));
  });

  it("DENIES self-write (prevents role self-escalation)", async () => {
    await assertFails(setDoc(doc(dbOps, `users/${SELF}`), { role: "ADMINISTRATOR" }));
  });

  it("DENIES admin client-write too (role changes go through the backend command)", async () => {
    await assertFails(setDoc(doc(dbAdmin, "users/user_other"), { role: "SERVICING_MANAGER" }));
  });
});
