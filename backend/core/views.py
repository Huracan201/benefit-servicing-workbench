"""Liveness & readiness endpoints — specs/16 §16.5.

- ``GET /health``     — liveness: the process is up. No dependency checks.
- ``GET /readiness``  — dependencies reachable (Firestore; Cloud Tasks is a
  Phase 3 seam, reported but non-fatal here). Backs Cloud Run health checks and
  the demo status page.

Both endpoints are unauthenticated: they override the project-default DRF auth
and permission classes with ``AllowAny`` and no authentication, so a probe never
needs a Firebase token.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from rest_framework import status
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
)
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response

logger = logging.getLogger(__name__)


@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def health(_request: Request) -> Response:
    """Liveness probe: always ``200 {"status": "ok"}`` if the process serves."""
    return Response({"status": "ok"})


# /readiness is UNAUTHENTICATED (Cloud Run probes need no token), so an unbounded
# flood would otherwise amplify 1 request -> 1 Firestore round-trip (cost / DoS —
# security-review-phase-3-4). Cache the dependency result for a short TTL so a burst
# collapses to at most one probe per window; Cloud Run's own frequent probes read the
# cache. A TTL (not a throttle) is used deliberately: throttling would reject the
# legitimate liveness/readiness probes. A ~5s window is well within probe tolerance.
_READINESS_TTL_SECONDS = 5.0
_readiness_cache: dict[str, Any] = {"at": 0.0, "result": None}


def _check_firestore() -> dict[str, Any]:
    """Best-effort Firestore reachability check, cached for ``_READINESS_TTL_SECONDS``.

    Uses the agreed seam ``common.firestore.get_client`` (emulator-aware). A tiny
    bounded read confirms the client can actually round-trip, not just construct.
    Import is lazy so a probe failure is reported, never an import error at module load.
    """
    now = time.monotonic()
    cached = _readiness_cache["result"]
    if cached is not None and (now - _readiness_cache["at"]) < _READINESS_TTL_SECONDS:
        return cached

    try:
        from common.firestore import get_client

        client = get_client()
        # Minimal round-trip against a throwaway collection; the doc need not
        # exist — a successful query proves reachability.
        next(iter(client.collection("_readiness_probe").limit(1).stream()), None)
        result: dict[str, Any] = {"status": "ok"}
    except Exception as exc:  # noqa: BLE001 - readiness must report, not raise
        logger.warning("readiness firestore check failed", exc_info=exc)
        result = {"status": "unavailable", "error": type(exc).__name__}

    _readiness_cache["at"] = now
    _readiness_cache["result"] = result
    return result


@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def readiness(_request: Request) -> Response:
    """Readiness probe: report dependency reachability.

    Returns ``200`` when every hard dependency (Firestore) is reachable, else
    ``503``. Cloud Tasks is wired in Phase 3 (specs/14); it is surfaced as
    ``not_configured`` and does not gate readiness in the foundation phase.
    """
    dependencies: dict[str, Any] = {
        "firestore": _check_firestore(),
        "cloudTasks": {"status": "not_configured"},  # Phase 3 — specs/14
    }

    hard_deps = ("firestore",)
    ok = all(dependencies[name]["status"] == "ok" for name in hard_deps)

    body = {"status": "ok" if ok else "unavailable", "dependencies": dependencies}
    http_status = status.HTTP_200_OK if ok else status.HTTP_503_SERVICE_UNAVAILABLE
    return Response(body, status=http_status)
