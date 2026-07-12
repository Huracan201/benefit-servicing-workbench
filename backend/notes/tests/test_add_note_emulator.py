"""Emulator integration tests for the add-servicing-note command (specs/10 §10.5).

Drives :func:`notes.services.add_note` directly against Firestore (no HTTP): a
note document is created under ``loans/{loanId}/notes`` with author attribution,
a ``MANUAL_NOTE_ADDED`` servicing event is appended, and an empty/whitespace-only
body is rejected.
"""

from __future__ import annotations

import os
import unittest

from django.test import SimpleTestCase, tag

from benefits.tests.domain_graph import count_events, make_ctx, seed_active_graph, unique_key
from commands.base import Unprocessable
from common.firestore import get_client
from notes.services import add_note
from repositories import loans


EMULATOR = bool(os.environ.get("FIRESTORE_EMULATOR_HOST"))


@tag("emulator")
@unittest.skipUnless(EMULATOR, "requires FIRESTORE_EMULATOR_HOST")
class AddNoteTests(SimpleTestCase):
    databases: list[str] = []

    def test_add_note_creates_doc_and_event(self):
        client = get_client()
        key = unique_key("note")
        g = seed_active_graph(client, key, term_months=1)

        result = add_note(
            loan_id=g.loan_id,
            text="  Called borrower re: missed payment  ",
            ctx=make_ctx(),
            client=client,
        )
        note_id = result["noteId"]
        # text is stripped
        self.assertEqual(result["text"], "Called borrower re: missed payment")
        self.assertEqual(result["authorId"], "user_test_manager")

        # --- note doc under loans/{loanId}/notes --------------------------
        snap = loans.note_ref(client, g.loan_id, note_id).get()
        self.assertTrue(snap.exists)
        note = snap.to_dict()
        self.assertEqual(note["loanId"], g.loan_id)
        self.assertEqual(note["text"], "Called borrower re: missed payment")
        self.assertEqual(note["authorName"], "Test Servicing Manager")

        # --- MANUAL_NOTE_ADDED event --------------------------------------
        self.assertEqual(
            count_events(client, event_type="MANUAL_NOTE_ADDED", loan_id=g.loan_id), 1
        )

    def test_empty_text_is_rejected(self):
        client = get_client()
        key = unique_key("noteempty")
        g = seed_active_graph(client, key, term_months=1)

        with self.assertRaises(Unprocessable):
            add_note(loan_id=g.loan_id, text="   ", ctx=make_ctx(), client=client)

        # no MANUAL_NOTE_ADDED event was written
        self.assertEqual(
            count_events(client, event_type="MANUAL_NOTE_ADDED", loan_id=g.loan_id), 0
        )
