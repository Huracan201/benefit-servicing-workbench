import { NextRequest, NextResponse } from "next/server";

// One-shared-password access gate for the PUBLIC demo deploy.
//
// It is a thin edge-side HTTP Basic Auth challenge, active ONLY when SITE_ACCESS_PASSWORD is
// set as an env var — so local `make demo`, CI, and the e2e suite (which never set it) stay
// completely open. On the Vercel deploy the var is set, so every request without the correct
// password gets a browser Basic-Auth prompt; the username is ignored, only the password is
// checked. This gates access to the demo (share one password with a reviewer) *on top of* the
// app's own role-based sign-in — it is not a replacement for it.
export function middleware(req: NextRequest) {
  const password = process.env.SITE_ACCESS_PASSWORD;
  if (!password) return NextResponse.next(); // gate disabled (local / CI / e2e)

  const header = req.headers.get("authorization") ?? "";
  if (header.startsWith("Basic ")) {
    try {
      const decoded = atob(header.slice(6)); // "username:password"
      const supplied = decoded.slice(decoded.indexOf(":") + 1);
      if (supplied === password) return NextResponse.next();
    } catch {
      // malformed header → fall through to the 401 challenge
    }
  }

  // NOTE: the realm value MUST be ASCII — a non-ASCII char (e.g. an em-dash) is an invalid
  // HTTP header value and causes the whole WWW-Authenticate header to be dropped, so the
  // browser never shows the password prompt (it just renders the body).
  return new NextResponse("Access to this demo requires the shared password.", {
    status: 401,
    headers: {
      "WWW-Authenticate": 'Basic realm="BenefitServicing demo (any username, shared password)"',
    },
  });
}

export const config = {
  // Challenge every page/route except Next internals + static assets.
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
