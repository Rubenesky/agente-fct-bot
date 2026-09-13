"""test_controller_seguimiento.py - Pruebas del comando /seguimiento y del
botón "❌ Cerrar" de contactos en controller.py.

No se llama a Telegram ni a git de verdad: update/context se simulan con
MagicMock (reply_text/edit_message_text como AsyncMock) y se mockean
db.* y git_sync.sync_offers_db.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import controller
from config import TELEGRAM_CHAT_ID


def make_update(text: str):
    update = MagicMock()
    update.effective_chat.id = TELEGRAM_CHAT_ID
    update.message.text = text
    update.message.reply_text = AsyncMock()
    return update


def make_context():
    return MagicMock()


def run(coro):
    return asyncio.run(coro)


class TestSeguimientoCommandParsing:
    def test_valid_syntax_with_note(self):
        update = make_update("/seguimiento Empresa X | 17/09/2026 | nota de prueba")

        with patch.object(controller.db, "set_followup", return_value=7) as mock_set, \
             patch.object(controller, "git_sync") as mock_git, \
             patch.object(controller.db, "get_connection") as mock_get_conn:
            fake_conn = MagicMock()
            fake_conn.execute.return_value.fetchone.return_value = {"attempt_count": 1}
            mock_get_conn.return_value.__enter__.return_value = fake_conn

            run(controller.seguimiento(update, make_context()))

        mock_set.assert_called_once_with("Empresa X", "2026-09-17", "nota de prueba")
        update.message.reply_text.assert_awaited_once()
        confirmation = update.message.reply_text.call_args.args[0]
        assert "Empresa X" in confirmation
        assert "17/09/2026" in confirmation
        mock_git.sync_offers_db.assert_called_once()

    def test_valid_syntax_without_note(self):
        update = make_update("/seguimiento Empresa Y | 01/12/2026")

        with patch.object(controller.db, "set_followup", return_value=8) as mock_set, \
             patch.object(controller, "git_sync"), \
             patch.object(controller.db, "get_connection") as mock_get_conn:
            fake_conn = MagicMock()
            fake_conn.execute.return_value.fetchone.return_value = {"attempt_count": 1}
            mock_get_conn.return_value.__enter__.return_value = fake_conn

            run(controller.seguimiento(update, make_context()))

        mock_set.assert_called_once_with("Empresa Y", "2026-12-01", None)

    def test_invalid_date_shows_help_and_does_not_save(self):
        update = make_update("/seguimiento Empresa Z | 2026-09-17")

        with patch.object(controller.db, "set_followup") as mock_set:
            run(controller.seguimiento(update, make_context()))

        mock_set.assert_not_called()
        update.message.reply_text.assert_awaited_once()
        help_text = update.message.reply_text.call_args.args[0]
        assert "/seguimiento" in help_text

    def test_missing_date_shows_help(self):
        update = make_update("/seguimiento Empresa sin fecha")

        with patch.object(controller.db, "set_followup") as mock_set:
            run(controller.seguimiento(update, make_context()))

        mock_set.assert_not_called()
        update.message.reply_text.assert_awaited_once()

    def test_no_args_shows_help(self):
        update = make_update("/seguimiento")

        with patch.object(controller.db, "set_followup") as mock_set:
            run(controller.seguimiento(update, make_context()))

        mock_set.assert_not_called()
        update.message.reply_text.assert_awaited_once()

    def test_unauthorized_chat_id_is_ignored(self):
        update = make_update("/seguimiento Empresa X | 17/09/2026")
        update.effective_chat.id = -1

        with patch.object(controller.db, "set_followup") as mock_set:
            run(controller.seguimiento(update, make_context()))

        mock_set.assert_not_called()
        update.message.reply_text.assert_not_awaited()


class TestCloseContactCallback:
    def test_closes_contact_and_edits_message(self):
        update = MagicMock()
        update.callback_query.answer = AsyncMock()
        update.callback_query.data = "close_contact:9"
        update.callback_query.message.chat.id = TELEGRAM_CHAT_ID
        update.callback_query.message.text_html = "🔔 Toca hacer seguimiento..."
        update.callback_query.edit_message_text = AsyncMock()

        with patch.object(controller.db, "close_contact") as mock_close, \
             patch.object(controller, "git_sync") as mock_git:
            run(controller.handle_close_contact(update, make_context()))

        mock_close.assert_called_once_with(9)
        update.callback_query.edit_message_text.assert_awaited_once()
        call = update.callback_query.edit_message_text.call_args
        text_arg = call.kwargs.get("text") or call.args[0]
        assert "Contacto cerrado" in text_arg
        mock_git.sync_offers_db.assert_called_once()

    def test_unauthorized_chat_id_is_ignored(self):
        update = MagicMock()
        update.callback_query.answer = AsyncMock()
        update.callback_query.data = "close_contact:9"
        update.callback_query.message.chat.id = -1
        update.callback_query.edit_message_text = AsyncMock()

        with patch.object(controller.db, "close_contact") as mock_close:
            run(controller.handle_close_contact(update, make_context()))

        mock_close.assert_not_called()
        update.callback_query.edit_message_text.assert_not_awaited()


class TestOlvidarCommand:
    def test_no_args_shows_help_and_does_not_call_anything(self):
        update = make_update("/olvidar")

        with patch.object(controller.db, "delete_contact_by_name") as mock_delete, \
             patch.object(controller, "git_sync") as mock_git:
            run(controller.olvidar(update, make_context()))

        mock_delete.assert_not_called()
        mock_git.sync_offers_db.assert_not_called()
        update.message.reply_text.assert_awaited_once()
        help_text = update.message.reply_text.call_args.args[0]
        assert "/olvidar" in help_text

    def test_existing_contact_confirms_deletion_and_syncs_git(self):
        update = make_update("/olvidar Empresa X")

        with patch.object(controller.db, "delete_contact_by_name", return_value=1) as mock_delete, \
             patch.object(controller, "git_sync") as mock_git:
            run(controller.olvidar(update, make_context()))

        mock_delete.assert_called_once_with("Empresa X")
        update.message.reply_text.assert_awaited_once()
        confirmation = update.message.reply_text.call_args.args[0]
        assert "Empresa X" in confirmation
        mock_git.sync_offers_db.assert_called_once()

    def test_nonexistent_contact_reports_not_found_and_does_not_sync_git(self):
        update = make_update("/olvidar Empresa Fantasma")

        with patch.object(controller.db, "delete_contact_by_name", return_value=0) as mock_delete, \
             patch.object(controller, "git_sync") as mock_git:
            run(controller.olvidar(update, make_context()))

        mock_delete.assert_called_once_with("Empresa Fantasma")
        update.message.reply_text.assert_awaited_once()
        response = update.message.reply_text.call_args.args[0]
        assert "Empresa Fantasma" in response
        mock_git.sync_offers_db.assert_not_called()

    def test_unauthorized_chat_id_is_ignored(self):
        update = make_update("/olvidar Empresa X")
        update.effective_chat.id = -1

        with patch.object(controller.db, "delete_contact_by_name") as mock_delete, \
             patch.object(controller, "git_sync") as mock_git:
            run(controller.olvidar(update, make_context()))

        mock_delete.assert_not_called()
        mock_git.sync_offers_db.assert_not_called()
        update.message.reply_text.assert_not_awaited()
