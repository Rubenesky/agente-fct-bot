"""test_db.py - Pruebas de las funciones de seguimiento de contactos en db.py
(set_followup, get_due_followups, clear_followup_date, close_contact).

Usa un fichero SQLite temporal (nunca offers.db real), parcheando db.DB_FILE
tal y como get_connection() lo usa internamente.
"""
from datetime import date, timedelta
from unittest.mock import patch

import pytest

import db


@pytest.fixture
def temp_db(tmp_path):
    db_file = str(tmp_path / "test_offers.db")
    with patch.object(db, "DB_FILE", db_file):
        db.init_db()
        yield db_file


class TestSetFollowup:
    def test_creates_new_contact_with_attempt_count_one(self, temp_db):
        rowid = db.set_followup("Empresa X", "2026-09-17", "primera nota")

        with db.get_connection() as conn:
            row = conn.execute("SELECT * FROM contacts WHERE rowid = ?", (rowid,)).fetchone()

        assert row["company"] == "Empresa X"
        assert row["attempt_count"] == 1
        assert row["next_followup_date"] == "2026-09-17"
        assert row["notes"] == "primera nota"
        assert row["status"] == "active"
        assert row["created_date"] is not None
        assert row["last_contact_date"] is not None

    def test_updates_existing_contact_case_insensitively_and_increments_attempts(self, temp_db):
        rowid1 = db.set_followup("Empresa X", "2026-09-17", "nota inicial")
        rowid2 = db.set_followup("empresa x", "2026-10-01")

        assert rowid1 == rowid2
        with db.get_connection() as conn:
            row = conn.execute("SELECT * FROM contacts WHERE rowid = ?", (rowid1,)).fetchone()
        assert row["attempt_count"] == 2
        assert row["next_followup_date"] == "2026-10-01"

        with db.get_connection() as conn:
            count = conn.execute("SELECT COUNT(*) AS c FROM contacts").fetchone()["c"]
        assert count == 1  # no duplica la fila, actualiza la existente

    def test_keeps_previous_notes_when_none_passed(self, temp_db):
        rowid = db.set_followup("Empresa Y", "2026-09-17", "nota original")
        db.set_followup("Empresa Y", "2026-10-01")

        with db.get_connection() as conn:
            row = conn.execute("SELECT notes FROM contacts WHERE rowid = ?", (rowid,)).fetchone()
        assert row["notes"] == "nota original"

    def test_overwrites_notes_when_new_value_given(self, temp_db):
        rowid = db.set_followup("Empresa Z", "2026-09-17", "nota original")
        db.set_followup("Empresa Z", "2026-10-01", "nota nueva")

        with db.get_connection() as conn:
            row = conn.execute("SELECT notes FROM contacts WHERE rowid = ?", (rowid,)).fetchone()
        assert row["notes"] == "nota nueva"


class TestGetDueFollowups:
    def test_returns_only_active_with_past_or_today_date(self, temp_db):
        today = date.today().isoformat()
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        tomorrow = (date.today() + timedelta(days=1)).isoformat()

        due_today = db.set_followup("Due Today", today)
        due_yesterday = db.set_followup("Due Yesterday", yesterday)
        db.set_followup("Due Tomorrow", tomorrow)  # futura: no debe salir

        # Activo pero sin fecha (NULL): no debe salir.
        sin_fecha = db.set_followup("Sin Fecha", today)
        with db.get_connection() as conn:
            conn.execute(
                "UPDATE contacts SET next_followup_date = NULL WHERE rowid = ?",
                (sin_fecha,),
            )

        # Fecha pasada pero contacto cerrado: no debe salir.
        cerrada = db.set_followup("Cerrada", yesterday)
        db.close_contact(cerrada)

        due = db.get_due_followups()
        due_rowids = {row["rowid"] for row in due}

        assert due_rowids == {due_today, due_yesterday}
        # Orden por next_followup_date (la más antigua primero).
        assert due[0]["rowid"] == due_yesterday


class TestClearFollowupDate:
    def test_sets_next_followup_date_to_null(self, temp_db):
        rowid = db.set_followup("Empresa Clear", "2026-09-17")
        db.clear_followup_date(rowid)

        with db.get_connection() as conn:
            row = conn.execute(
                "SELECT next_followup_date, status FROM contacts WHERE rowid = ?", (rowid,)
            ).fetchone()
        assert row["next_followup_date"] is None
        assert row["status"] == "active"  # clear_followup_date no cierra el contacto


class TestCloseContact:
    def test_sets_status_to_closed(self, temp_db):
        rowid = db.set_followup("Empresa Close", "2026-09-17")
        db.close_contact(rowid)

        with db.get_connection() as conn:
            row = conn.execute("SELECT status FROM contacts WHERE rowid = ?", (rowid,)).fetchone()
        assert row["status"] == "closed"
