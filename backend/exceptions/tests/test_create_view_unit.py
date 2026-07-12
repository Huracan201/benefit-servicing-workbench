"""Pure view-layer unit tests for ``POST /exceptions`` input validation (specs/11 §11.4).

These are ``@tag('unit')`` tests — no Firestore, no emulator. They pin the up-front
request validation :class:`exceptions.views.CreateExceptionView` performs BEFORE any
transaction, so a malformed ``entityType`` / ``entityId`` is a clean ``400``
(specs/11 §11.3) instead of an uncaught ``500``: ``create_exception`` scopes only
``{LOAN, BORROWER, EMPLOYER}`` and feeds ``entityId`` straight to Firestore
``.document()``, which raises a bare ``ValueError`` on a '/' (path separator), a lone
'.'/'..', or the reserved ``__*__`` id pattern.

The command layer is mocked, so each test exercises ONLY the view guard and asserts
both the response AND that the command was (not) reached — a regression that removed
the guard would let ``create_exception`` be called (and 500 downstream against real
Firestore), tripping ``assert_not_called`` here. ``SimpleTestCase`` + ``databases =
[]`` keeps them database-free and fast (matching ``payments/tests/
test_command_layer_unit.py``).
"""

from __future__ import annotations

from unittest import mock

from django.test import SimpleTestCase, tag
from rest_framework.test import APIRequestFactory, force_authenticate

from common.enums import ExceptionType
from exceptions.views import CreateExceptionView
from firebase_auth.authentication import FirebasePrincipal

# A VALID exceptionType — the enum check runs before the entityType/entityId
# guards, so these fixtures must clear it to reach the code under test.
_VALID_TYPE = str(ExceptionType.EMPLOYMENT_VERIFICATION_REQUIRED)
_CLEAN_LOAN_ID = "loan_jordan_lee"  # specs seed id shape: prefix + underscore key

# Patch target: the view looks the command up as ``exception_commands
# .create_exception`` (an attribute of the exceptions.commands module), so
# patching the module attribute intercepts it without importing views' alias.
_CREATE = "exceptions.commands.create_exception"


def _operations_principal() -> FirebasePrincipal:
    """A minimal authenticated OPERATIONS_USER principal (clears RequireOperations)."""
    return FirebasePrincipal(uid="u_ops", claims={"role": "OPERATIONS_USER", "name": "Op"})


@tag("unit")
class CreateExceptionValidationTests(SimpleTestCase):
    databases: list[str] = []

    def setUp(self):
        self.factory = APIRequestFactory()
        self.view = CreateExceptionView.as_view()

    def _post(self, body: dict):
        request = self.factory.post(
            "/exceptions", body, format="json", HTTP_IDEMPOTENCY_KEY="k-unit-1"
        )
        force_authenticate(request, user=_operations_principal())
        return self.view(request)

    @mock.patch(_CREATE)
    def test_entity_id_with_slash_is_400_not_500(self, mock_create):
        # Pre-fix: entityId "loan/evil" reaches loans.ref(...).document() ->
        # ValueError -> uncaught 500. Now rejected up-front as a clean 400, and
        # the command is never reached (proving the guard, not the mock, fired).
        response = self._post(
            {
                "exceptionType": _VALID_TYPE,
                "entityType": "LOAN",
                "entityId": "loan/evil",
                "summary": "s",
            }
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"]["code"], "VALIDATION_ERROR")
        mock_create.assert_not_called()

    @mock.patch(_CREATE)
    def test_unknown_entity_type_is_400(self, mock_create):
        response = self._post(
            {
                "exceptionType": _VALID_TYPE,
                "entityType": "FOO",
                "entityId": _CLEAN_LOAN_ID,
                "summary": "s",
            }
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"]["code"], "VALIDATION_ERROR")
        mock_create.assert_not_called()

    @mock.patch(_CREATE)
    def test_reserved_double_underscore_id_is_400(self, mock_create):
        # __id__ passes the id charset (underscores are legal) but is a Firestore
        # reserved pattern — the explicit __*__ guard rejects it.
        response = self._post(
            {
                "exceptionType": _VALID_TYPE,
                "entityType": "LOAN",
                "entityId": "__id__",
                "summary": "s",
            }
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"]["code"], "VALIDATION_ERROR")
        mock_create.assert_not_called()

    @mock.patch(_CREATE)
    def test_dotdot_id_is_400(self, mock_create):
        response = self._post(
            {
                "exceptionType": _VALID_TYPE,
                "entityType": "LOAN",
                "entityId": "..",
                "summary": "s",
            }
        )
        self.assertEqual(response.status_code, 400)
        mock_create.assert_not_called()

    @mock.patch(_CREATE)
    def test_valid_loan_clean_id_succeeds(self, mock_create):
        # The happy path is UNCHANGED: a clean {LOAN, id} passes the guard and the
        # command runs (mocked to avoid Firestore) -> 200.
        mock_create.return_value = {"exceptionId": "exc_1", "status": "OPEN"}
        response = self._post(
            {
                "exceptionType": _VALID_TYPE,
                "entityType": "LOAN",
                "entityId": _CLEAN_LOAN_ID,
                "summary": "manual check",
            }
        )
        self.assertEqual(response.status_code, 200)
        mock_create.assert_called_once()
        kwargs = mock_create.call_args.kwargs
        self.assertEqual(kwargs["entity_type"], "LOAN")
        self.assertEqual(kwargs["entity_id"], _CLEAN_LOAN_ID)

    @mock.patch(_CREATE)
    def test_entity_type_normalized_to_upper_before_command(self, mock_create):
        # A lowercase entityType is accepted and passed to the command canonicalized
        # (so the persisted entityType is always upper-case).
        mock_create.return_value = {"exceptionId": "exc_2", "status": "OPEN"}
        response = self._post(
            {
                "exceptionType": _VALID_TYPE,
                "entityType": "borrower",
                "entityId": "bor_jordan_lee",
                "summary": "s",
            }
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_create.call_args.kwargs["entity_type"], "BORROWER")
