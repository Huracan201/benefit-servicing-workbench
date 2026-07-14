"""Pure unit tests (``@tag('unit')``) for the Phase-5 defense-in-depth hardening
(security review §7 #9): correlation-id sanitization + the security response headers.

Offline, database-free (``SimpleTestCase`` + ``RequestFactory``) — no emulator.
"""

from __future__ import annotations

from unittest.mock import patch

from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings, tag

from core import views as core_views
from core.logging_utils import CORRELATION_ID_HEADER, CORRELATION_ID_META_KEY
from core.middleware import CorrelationIdMiddleware, SecurityHeadersMiddleware

_UUID4_HEX = r"\A[0-9a-f]{32}\Z"


def _ok(_request) -> HttpResponse:
    return HttpResponse("ok")


@tag("unit")
class CorrelationIdSanitizationTests(SimpleTestCase):
    databases: list[str] = []

    def setUp(self) -> None:
        self.rf = RequestFactory()
        self.mw = CorrelationIdMiddleware(_ok)

    def _run(self, inbound: str | None = None) -> tuple[str, str]:
        extra = {} if inbound is None else {CORRELATION_ID_META_KEY: inbound}
        request = self.rf.get("/api/v1/health", **extra)
        response = self.mw(request)
        # The id set on the request and the id echoed on the response must always match.
        return request.correlation_id, response[CORRELATION_ID_HEADER]

    def test_absent_mints_uuid(self) -> None:
        cid, echoed = self._run()
        self.assertEqual(cid, echoed)
        self.assertRegex(cid, _UUID4_HEX)

    def test_well_formed_inbound_is_honored(self) -> None:
        # A caller / an /internal task carrying the originating id keeps it across hops.
        for value in ("abc-123_DEF.456", "0123456789abcdef0123456789abcdef", "a"):
            cid, echoed = self._run(value)
            self.assertEqual(cid, value)
            self.assertEqual(echoed, value)

    def test_crlf_injection_is_rejected(self) -> None:
        # A CRLF-bearing value must NOT be honored — else it forges log lines / injects a
        # second response header. It is discarded and a fresh id minted.
        cid, echoed = self._run("evil\r\nSet-Cookie: pwned=1")
        self.assertNotIn("\n", cid)
        self.assertNotIn("\r", cid)
        self.assertRegex(cid, _UUID4_HEX)
        self.assertEqual(cid, echoed)

    def test_control_chars_and_spaces_rejected(self) -> None:
        for bad in ("bad\tvalue", "has spaces", "semi;colon", "<script>", "quote\"x"):
            cid, _ = self._run(bad)
            self.assertRegex(cid, _UUID4_HEX)

    def test_oversized_rejected(self) -> None:
        cid, _ = self._run("a" * 200)
        self.assertRegex(cid, _UUID4_HEX)


@tag("unit")
class SecurityHeadersTests(SimpleTestCase):
    databases: list[str] = []

    def test_headers_present(self) -> None:
        response = SecurityHeadersMiddleware(_ok)(RequestFactory().get("/api/v1/loans"))
        self.assertEqual(response["X-Frame-Options"], "DENY")
        csp = response["Content-Security-Policy"]
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertIn("default-src 'none'", csp)
        self.assertIn("base-uri 'none'", csp)

    def test_does_not_clobber_a_view_override(self) -> None:
        def _framed(_request: object) -> HttpResponse:
            r = HttpResponse("ok")
            r["X-Frame-Options"] = "SAMEORIGIN"
            return r

        response = SecurityHeadersMiddleware(_framed)(RequestFactory().get("/"))
        self.assertEqual(response["X-Frame-Options"], "SAMEORIGIN")


@tag("unit")
class WiredResponseHeadersTests(SimpleTestCase):
    """Through the REAL middleware stack (SecurityMiddleware + SecurityHeadersMiddleware +
    CorrelationIdMiddleware) to the liveness endpoint — proves the settings + ordering are
    wired, not just the classes in isolation."""

    databases: list[str] = []

    def test_health_response_carries_every_security_header(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        # From django.middleware.security.SecurityMiddleware via the SECURE_* settings:
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response["Referrer-Policy"], "strict-origin-when-cross-origin")
        # From core.middleware.SecurityHeadersMiddleware:
        self.assertEqual(response["X-Frame-Options"], "DENY")
        self.assertIn("frame-ancestors 'none'", response["Content-Security-Policy"])
        # From core.middleware.CorrelationIdMiddleware (a fresh id was minted + echoed):
        self.assertTrue(response.get(CORRELATION_ID_HEADER))

    def test_dev_default_does_not_force_https_redirect(self) -> None:
        # SECURE_SSL_REDIRECT is production-only, so the http health probe returns 200,
        # never a 301 (which would break Cloud Run's http liveness check).
        self.assertEqual(self.client.get("/health").status_code, 200)


class _FakeFirestore:
    """A get_client() stand-in whose stream() is empty (a successful reachability probe)."""

    def collection(self, _name: str) -> "_FakeFirestore":
        return self

    def limit(self, _n: int) -> "_FakeFirestore":
        return self

    def stream(self):
        return iter(())


@tag("unit")
class ReadinessCacheTests(SimpleTestCase):
    """The unauthenticated /readiness Firestore probe is TTL-cached so a burst/flood
    collapses to one dependency round-trip (security-review-phase-3-4)."""

    databases: list[str] = []

    def _reset(self) -> None:
        core_views._readiness_cache["at"] = 0.0
        core_views._readiness_cache["result"] = None

    def setUp(self) -> None:
        self._reset()

    def tearDown(self) -> None:
        self._reset()

    def test_probe_is_cached_within_ttl(self) -> None:
        calls = {"n": 0}

        def _fake():
            calls["n"] += 1
            return _FakeFirestore()

        with patch("common.firestore.get_client", _fake):
            r1 = core_views._check_firestore()
            r2 = core_views._check_firestore()
        self.assertEqual(r1, {"status": "ok"})
        self.assertEqual(r2, {"status": "ok"})
        self.assertEqual(calls["n"], 1)  # the second call is served from cache

    def test_cache_refreshes_after_ttl(self) -> None:
        calls = {"n": 0}

        def _fake():
            calls["n"] += 1
            return _FakeFirestore()

        with patch("common.firestore.get_client", _fake):
            core_views._check_firestore()
            # age the cache past the TTL, forcing a fresh probe
            core_views._readiness_cache["at"] -= core_views._READINESS_TTL_SECONDS + 1
            core_views._check_firestore()
        self.assertEqual(calls["n"], 2)


@tag("unit")
class ReadinessCloudTasksReportingTests(SimpleTestCase):
    """/readiness reports Cloud Tasks as a CONFIGURATION status (a config reflection, not a
    live ping — enqueuing a probe would have side effects), mirroring internal.enqueue's
    TASK_EXECUTION_MODE dispatch + the /internal OIDC env. Non-gating in every case."""

    databases: list[str] = []

    @override_settings(TASK_EXECUTION_MODE="inline")
    def test_inline_is_not_configured(self) -> None:
        # Emulator / local / CI: the async surface runs in-process, so Cloud Tasks is
        # intentionally absent — reported, not a fault.
        self.assertEqual(core_views._cloud_tasks_status(), {"status": "not_configured"})

    @override_settings(
        TASK_EXECUTION_MODE="cloud",
        TASKS_AUDIENCE="https://bsw-api-xyz.run.app",
        TASKS_INVOKER_SA="bsw-invoker@demo.iam.gserviceaccount.com",
    )
    def test_cloud_with_oidc_env_is_configured(self) -> None:
        self.assertEqual(core_views._cloud_tasks_status(), {"status": "configured"})

    @override_settings(TASK_EXECUTION_MODE="cloud", TASKS_AUDIENCE="", TASKS_INVOKER_SA="")
    def test_cloud_missing_env_is_unavailable(self) -> None:
        # A real misconfiguration: cloud dispatch on but the OIDC audience/invoker unset
        # would have tasks rejected at the /internal boundary — surface it.
        result = core_views._cloud_tasks_status()
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("TASKS_AUDIENCE", result["error"])

    @override_settings(TASK_EXECUTION_MODE="cloud", TASKS_AUDIENCE="", TASKS_INVOKER_SA="")
    def test_cloud_tasks_unavailable_does_not_gate_readiness(self) -> None:
        # Even a cloudTasks 'unavailable' must not flip the top-level status to 503;
        # firestore is the only hard dependency (specs/16 §16.5).
        with patch.object(core_views, "_check_firestore", return_value={"status": "ok"}):
            response = self.client.get("/readiness")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["dependencies"]["firestore"]["status"], "ok")
        self.assertEqual(body["dependencies"]["cloudTasks"]["status"], "unavailable")


@tag("unit")
class RendererConfigTests(SimpleTestCase):
    databases: list[str] = []

    def test_dev_keeps_browsable_api_json_first(self) -> None:
        from django.conf import settings

        renderers = settings.REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"]
        # JSON is always first (the API default); dev/CI additionally keep the browsable API.
        self.assertEqual(renderers[0], "rest_framework.renderers.JSONRenderer")
        self.assertIn("rest_framework.renderers.BrowsableAPIRenderer", renderers)
