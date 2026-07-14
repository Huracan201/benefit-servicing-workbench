"use client";

// /signin — the minimal EMULATOR sign-in screen (specs/18 §18.1). Not production
// authentication: it signs in against the local Firebase Auth emulator using one of
// the three seeded demo accounts, then redirects to the dashboard. On-brand with the
// part-1 kit (Card, Button, design tokens); a failure surfaces typed, operator-facing
// copy in-line (session.ts never throws).

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import Card from "@/components/Card";
import Button from "@/components/Button";
import { usingEmulator } from "@/lib/firebase";
import { signInWithEmulator } from "@/lib/session";

// Mirrors backend/seed/users.py (DEMO_USERS + DEFAULT_PASSWORD) — the seeded emulator
// accounts. The seed module is the source of truth; keep these in sync with it.
const DEMO_PASSWORD = "DemoPass!234";
const DEMO_ACCOUNTS: ReadonlyArray<{ email: string; role: string }> = [
  { email: "ops@demo.test", role: "Operations User" },
  { email: "mgr@demo.test", role: "Servicing Manager" },
  { email: "admin@demo.test", role: "Administrator" },
];

const INPUT_CLASS =
  "h-9 w-full rounded-sm border border-border bg-surface-2 px-2.5 text-sm text-ink " +
  "placeholder:text-ink-3 focus-visible:border-accent focus-visible:outline " +
  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent";

export default function SignInPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting) return;
    setError(null);
    setSubmitting(true);
    const result = await signInWithEmulator(email, password);
    if (result.ok) {
      // Keep `submitting` true through the navigation so the button never re-enables.
      router.push("/");
      return;
    }
    setError(result.message);
    setSubmitting(false);
  }

  function fillDemo(demoEmail: string) {
    setEmail(demoEmail);
    setPassword(DEMO_PASSWORD);
    setError(null);
  }

  return (
    <div className="mx-auto w-full max-w-md py-8">
      {/* Brand lockup (matches the AppShell sidebar brand block). */}
      <div className="mb-4 flex items-center gap-2.5">
        <span
          aria-hidden="true"
          className="grid h-[26px] w-[26px] shrink-0 place-items-center rounded-[7px] bg-accent text-[14px] font-bold text-accent-ink"
        >
          B
        </span>
        <span className="font-display text-h2 font-semibold text-ink">
          BenefitServicing Workbench
        </span>
      </div>

      <Card title="Sign in" meta="Sign in to the operations workbench.">
        <form onSubmit={onSubmit} className="space-y-3" noValidate>
          <label className="block">
            <span className="mb-1 block text-xs font-medium text-ink-2">Email</span>
            <input
              type="email"
              name="email"
              autoComplete="username"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="ops@demo.test"
              className={INPUT_CLASS}
            />
          </label>

          <label className="block">
            <span className="mb-1 block text-xs font-medium text-ink-2">Password</span>
            <input
              type="password"
              name="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••"
              className={INPUT_CLASS}
            />
          </label>

          {error ? (
            <p
              role="alert"
              className="rounded-sm border border-critical/[0.35] bg-critical/[0.12] px-2.5 py-2 text-sm text-critical"
            >
              {error}
            </p>
          ) : null}

          {/* Full-width submit: the Button wraps its <button> in an inline-flex <span>;
              stretch that span so the inner w-full button fills the form row. */}
          <div className="[&>span]:w-full">
            <Button type="submit" loading={submitting} className="w-full">
              {submitting ? "Signing in…" : "Sign in"}
            </Button>
          </div>
        </form>

        {/* Demo accounts hint — click a row to fill the form. */}
        <div className="mt-4 border-t border-border pt-3">
          <p className="text-xs font-semibold uppercase tracking-wider text-ink-3">
            Demo accounts
          </p>
          <p className="mt-1 text-xs text-ink-3">
            Shared password{" "}
            <code className="font-mono text-ink-2">{DEMO_PASSWORD}</code> · click a
            row to fill.
          </p>
          <ul className="mt-2 space-y-1">
            {DEMO_ACCOUNTS.map((account) => (
              <li key={account.email}>
                <button
                  type="button"
                  onClick={() => fillDemo(account.email)}
                  className="flex w-full items-center justify-between gap-2 rounded-sm border border-border bg-surface-2 px-2.5 py-1.5 text-left text-sm transition-colors hover:border-accent/[0.4] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                >
                  <span className="font-mono text-ink-2">{account.email}</span>
                  <span className="text-xs text-ink-3">{account.role}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>

        {/* Environment label — accurate whether wired to the emulator (dev/CI) or a live demo. */}
        <p className="mt-3 flex items-center gap-1.5 text-xs text-ink-3">
          <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-info" />
          {usingEmulator
            ? "Local Auth emulator · not production authentication."
            : "Demo environment · synthetic data, resets daily."}
        </p>
      </Card>
    </div>
  );
}
