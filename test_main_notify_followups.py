"""test_main_notify_followups.py - Pruebas de notify_due_followups (main.py):
avisa por Telegram los contactos con seguimiento pendiente (db.get_due_followups)
y limpia next_followup_date (db.clear_followup_date) tras notificar cada uno.

No se llama a la API real de Telegram: se mockea requests.post.
"""
from unittest.mock import MagicMock, patch

import main


def make_contact(rowid=1, company="Empresa X", attempt_count=1):
    return {
        "rowid": rowid,
        "company": company,
        "contact_name": None,
        "channel": None,
        "notes": None,
        "attempt_count": attempt_count,
        "next_followup_date": "2026-09-12",
        "status": "active",
        "created_date": "2026-09-01T10:00:00",
        "last_contact_date": "2026-09-01T10:00:00",
    }


class TestNotifyDueFollowups:
    def test_clears_followup_date_after_notifying(self):
        contact = make_contact(rowid=5)

        with patch.object(main.db, "get_due_followups", return_value=[contact]), \
             patch.object(main.requests, "post") as mock_post, \
             patch.object(main.db, "clear_followup_date") as mock_clear:
            mock_post.return_value = MagicMock(status_code=200)
            main.notify_due_followups()

        mock_post.assert_called_once()
        mock_clear.assert_called_once_with(5)

    def test_includes_warning_when_attempt_count_at_least_three(self):
        contact = make_contact(attempt_count=3)

        with patch.object(main.db, "get_due_followups", return_value=[contact]), \
             patch.object(main.requests, "post") as mock_post, \
             patch.object(main.db, "clear_followup_date"):
            mock_post.return_value = MagicMock(status_code=200)
            main.notify_due_followups()

        sent_text = mock_post.call_args.kwargs["json"]["text"]
        assert "intento nº 3" in sent_text
        assert "plantéate si merece la pena seguir insistiendo" in sent_text

    def test_no_warning_when_attempt_count_below_three(self):
        contact = make_contact(attempt_count=2)

        with patch.object(main.db, "get_due_followups", return_value=[contact]), \
             patch.object(main.requests, "post") as mock_post, \
             patch.object(main.db, "clear_followup_date"):
            mock_post.return_value = MagicMock(status_code=200)
            main.notify_due_followups()

        sent_text = mock_post.call_args.kwargs["json"]["text"]
        assert "plantéate si merece la pena seguir insistiendo" not in sent_text

    def test_includes_close_contact_button_with_rowid(self):
        contact = make_contact(rowid=42)

        with patch.object(main.db, "get_due_followups", return_value=[contact]), \
             patch.object(main.requests, "post") as mock_post, \
             patch.object(main.db, "clear_followup_date"):
            mock_post.return_value = MagicMock(status_code=200)
            main.notify_due_followups()

        keyboard = mock_post.call_args.kwargs["json"]["reply_markup"]["inline_keyboard"]
        assert keyboard[0][0]["callback_data"] == "close_contact:42"

    def test_does_nothing_when_no_due_followups(self):
        with patch.object(main.db, "get_due_followups", return_value=[]), \
             patch.object(main.requests, "post") as mock_post, \
             patch.object(main.db, "clear_followup_date") as mock_clear:
            main.notify_due_followups()

        mock_post.assert_not_called()
        mock_clear.assert_not_called()

    def test_notifies_each_contact_and_clears_each_rowid(self):
        contacts = [make_contact(rowid=1), make_contact(rowid=2, company="Empresa Y")]

        with patch.object(main.db, "get_due_followups", return_value=contacts), \
             patch.object(main.requests, "post") as mock_post, \
             patch.object(main.db, "clear_followup_date") as mock_clear:
            mock_post.return_value = MagicMock(status_code=200)
            main.notify_due_followups()

        assert mock_post.call_count == 2
        assert mock_clear.call_count == 2
        cleared_rowids = {call.args[0] for call in mock_clear.call_args_list}
        assert cleared_rowids == {1, 2}
