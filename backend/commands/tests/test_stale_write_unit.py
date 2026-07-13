"""Pure unit tests (``@tag('unit')``) for the ``If-Match`` optimistic-concurrency
precondition (specs/08 §8.4). Database-free — the enforcement helper is pure.
"""

from __future__ import annotations

from django.test import SimpleTestCase, tag

from commands.base import CommandContext, StaleWrite, assert_expected_revision


def _ctx(expected):
    return CommandContext(
        actor_id="u1",
        actor_role="SERVICING_MANAGER",
        actor_name="Op",
        expected_revision=expected,
    )


@tag("unit")
class AssertExpectedRevisionTests(SimpleTestCase):
    databases: list[str] = []

    def test_none_is_a_noop(self) -> None:
        # No If-Match sent → no precondition, whatever the entity's revision.
        assert_expected_revision({"revision": 7}, _ctx(None))

    def test_matching_revision_passes(self) -> None:
        assert_expected_revision({"revision": 5}, _ctx(5))

    def test_mismatch_raises_stale_write_409(self) -> None:
        with self.assertRaises(StaleWrite) as caught:
            assert_expected_revision({"revision": 6}, _ctx(5))
        self.assertEqual(caught.exception.http_status, 409)
        self.assertEqual(caught.exception.code, "STALE_WRITE")

    def test_missing_revision_is_a_mismatch(self) -> None:
        # An entity with no revision cannot satisfy a concrete expectation.
        with self.assertRaises(StaleWrite):
            assert_expected_revision({}, _ctx(5))
