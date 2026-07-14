import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { activateBenefit, sendCommand } from "@/lib/commandClient";
import { CommandError, isCommandError } from "@/lib/errors";

// The command client pulls its ID token from the shared Firebase client; mock it so no
// real auth/network is required.
vi.mock("@/lib/firebase", () => ({
  getFirebaseAuth: vi.fn(),
}));

import { getFirebaseAuth } from "@/lib/firebase";

const mockedGetAuth = vi.mocked(getFirebaseAuth);

/** Build a signed-in auth stub whose getIdToken resolves `token`. */
function signIn(token = "tok-123"): void {
  mockedGetAuth.mockReturnValue({
    currentUser: { getIdToken: vi.fn().mockResolvedValue(token) },
  } as unknown as ReturnType<typeof getFirebaseAuth>);
}

function signOut(): void {
  mockedGetAuth.mockReturnValue({
    currentUser: null,
  } as unknown as ReturnType<typeof getFirebaseAuth>);
}

function jsonResponse(
  status: number,
  body: unknown,
  headers: Record<string, string> = {},
): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

/** Pull the outgoing header map off a recorded fetch call. */
function headersOfCall(call: unknown[]): Record<string, string> {
  const init = call[1] as RequestInit;
  return init.headers as Record<string, string>;
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  process.env.NEXT_PUBLIC_API_BASE_URL = "http://api.test";
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  signIn();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("sendCommand", () => {
  it("POSTs to /api/v1 with auth, idempotency and content-type headers", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(200, { id: "ben_1", status: "ACTIVE", revision: 3 }),
    );

    const outcome = await activateBenefit("ben_1");

    expect(outcome.status).toBe("completed");
    if (outcome.status === "completed") {
      expect(outcome.data.id).toBe("ben_1");
      expect(outcome.httpStatus).toBe(200);
    }

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://api.test/api/v1/benefit-agreements/ben_1/activate");
    expect((init as RequestInit).method).toBe("POST");
    const headers = headersOfCall(fetchMock.mock.calls[0]);
    expect(headers.Authorization).toBe("Bearer tok-123");
    expect(headers["Content-Type"]).toBe("application/json");
    expect(headers["Idempotency-Key"]).toBeTruthy();
  });

  it("uses a caller-supplied Idempotency-Key verbatim and sets If-Match", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { id: "ben_1" }));

    await activateBenefit("ben_1", undefined, { idempotencyKey: "my-key", expectedRevision: 12 });

    const headers = headersOfCall(fetchMock.mock.calls[0]);
    expect(headers["Idempotency-Key"]).toBe("my-key");
    expect(headers["If-Match"]).toBe("12");
  });

  it("reuses the SAME Idempotency-Key across a 202 poll then completes", async () => {
    fetchMock
      .mockResolvedValueOnce(
        jsonResponse(202, { state: "IN_PROGRESS", operation: "ACTIVATE_BENEFIT" }, {
          "Retry-After": "0",
        }),
      )
      .mockResolvedValueOnce(jsonResponse(200, { id: "ben_1", status: "ACTIVE" }));

    const outcome = await activateBenefit("ben_1");

    expect(outcome.status).toBe("completed");
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const first = headersOfCall(fetchMock.mock.calls[0])["Idempotency-Key"];
    const second = headersOfCall(fetchMock.mock.calls[1])["Idempotency-Key"];
    expect(second).toBe(first);
  });

  it("returns a pending outcome once the poll budget is exhausted", async () => {
    // A fresh Response per call — a body can only be read once.
    fetchMock.mockImplementation(async () =>
      jsonResponse(202, { status: "IN_PROGRESS", retryAfter: 0, correlationId: "c1" }, {
        "Retry-After": "0",
      }),
    );

    const outcome = await sendCommand("benefit-agreements/ben_1/activate", { maxPolls: 1 });

    expect(outcome.status).toBe("pending");
    if (outcome.status === "pending") {
      expect(outcome.operation?.status).toBe("IN_PROGRESS");
    }
    expect(fetchMock).toHaveBeenCalledTimes(2); // initial + 1 poll
  });

  it("maps a 409 STALE_WRITE envelope to a typed CommandError with human copy", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(409, {
        error: { code: "STALE_WRITE", message: "revision mismatch", correlationId: "cid-9" },
      }),
    );

    let caught: unknown;
    try {
      await activateBenefit("ben_1", undefined, { expectedRevision: 1 });
    } catch (e) {
      caught = e;
    }

    expect(isCommandError(caught)).toBe(true);
    const err = caught as CommandError;
    expect(err.code).toBe("STALE_WRITE");
    expect(err.httpStatus).toBe(409);
    expect(err.correlationId).toBe("cid-9");
    expect(err.userMessage).toMatch(/refresh/i);
  });

  it("falls back to a status-derived code when there is no envelope", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(403, {}));

    await expect(activateBenefit("ben_1")).rejects.toMatchObject({ code: "FORBIDDEN" });
  });

  it("throws UNAUTHENTICATED without calling fetch when no user is signed in", async () => {
    signOut();

    let caught: unknown;
    try {
      await activateBenefit("ben_1");
    } catch (e) {
      caught = e;
    }

    expect(isCommandError(caught)).toBe(true);
    expect((caught as CommandError).code).toBe("UNAUTHENTICATED");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("wraps a transport failure as NETWORK_ERROR", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));

    await expect(activateBenefit("ben_1")).rejects.toMatchObject({ code: "NETWORK_ERROR" });
  });
});
