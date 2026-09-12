"""test_controller_decision.py - Pruebas de handle_decision en controller.py,
el callback que procesa los botones ✅ Aprobar / ❌ Descartar de cada oferta
(callback_data="approve:<rowid>" / "reject:<rowid>") - el flujo más usado de
todo el bot.

Mismo estilo que test_controller_seguimiento.py: no se llama a Telegram ni a
git de verdad (update/callback_query se simulan con MagicMock,
answer/edit_message_text como AsyncMock) y se mockean db.* y
git_sync.sync_offers_db.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import controller
from config import TELEGRAM_CHAT_ID


def make_offer_row(rowid: int) -> dict:
    return {
        "rowid": rowid,
        "title": "Prácticas DAW en Empresa X",
        "url": f"https://ejemplo.test/oferta-{rowid}",
        "company": "Empresa X",
        "location": "Granada",
        "mode": "Presencial",
        "source": "Adzuna",
        "description": "",
        "tier": "A",
        "justification": "encaja con el perfil",
        "status": "pending",
        "found_date": "2026-09-01T10:00:00",
        "decided_date": None,
    }


def make_update(callback_data: str, chat_id=TELEGRAM_CHAT_ID):
    update = MagicMock()
    update.callback_query.answer = AsyncMock()
    update.callback_query.data = callback_data
    update.callback_query.message.chat.id = chat_id
    update.callback_query.message.text_html = "🅰️ <b>Tier A</b> — <b>Prácticas DAW</b>"
    update.callback_query.edit_message_text = AsyncMock()
    return update


def make_context():
    return MagicMock()


def run(coro):
    return asyncio.run(coro)


class TestApproveOffer:
    def test_approves_offer_updates_status_edits_message_and_syncs_git(self):
        update = make_update("approve:5")

        with patch.object(controller.db, "get_offer_by_rowid", return_value=make_offer_row(5)) as mock_get, \
             patch.object(controller.db, "set_status") as mock_set_status, \
             patch.object(controller, "git_sync") as mock_git:
            run(controller.handle_decision(update, make_context()))

        mock_get.assert_called_once_with(5)
        mock_set_status.assert_called_once_with(5, "approved")

        update.callback_query.edit_message_text.assert_awaited_once()
        call = update.callback_query.edit_message_text.call_args
        text_arg = call.kwargs.get("text") or call.args[0]
        assert "APROBADA" in text_arg
        assert call.kwargs.get("parse_mode") == "HTML"

        mock_git.sync_offers_db.assert_called_once()
        assert "rowid=5" in mock_git.sync_offers_db.call_args.kwargs.get("reason", "")


class TestRejectOffer:
    def test_rejects_offer_updates_status_edits_message_and_syncs_git(self):
        update = make_update("reject:9")

        with patch.object(controller.db, "get_offer_by_rowid", return_value=make_offer_row(9)), \
             patch.object(controller.db, "set_status") as mock_set_status, \
             patch.object(controller, "git_sync") as mock_git:
            run(controller.handle_decision(update, make_context()))

        mock_set_status.assert_called_once_with(9, "rejected")

        update.callback_query.edit_message_text.assert_awaited_once()
        call = update.callback_query.edit_message_text.call_args
        text_arg = call.kwargs.get("text") or call.args[0]
        assert "DESCARTADA" in text_arg

        mock_git.sync_offers_db.assert_called_once()


class TestOfferNotFound:
    def test_offer_not_found_edits_message_and_does_not_touch_status_or_git(self):
        update = make_update("approve:404")

        with patch.object(controller.db, "get_offer_by_rowid", return_value=None), \
             patch.object(controller.db, "set_status") as mock_set_status, \
             patch.object(controller, "git_sync") as mock_git:
            run(controller.handle_decision(update, make_context()))

        mock_set_status.assert_not_called()
        mock_git.sync_offers_db.assert_not_called()

        update.callback_query.edit_message_text.assert_awaited_once()
        call = update.callback_query.edit_message_text.call_args
        text_arg = call.kwargs.get("text") or call.args[0]
        assert "no encontrada" in text_arg.lower()


class TestUnauthorizedChat:
    def test_unauthorized_chat_id_is_ignored(self):
        update = make_update("approve:5", chat_id=-1)

        with patch.object(controller.db, "get_offer_by_rowid") as mock_get, \
             patch.object(controller.db, "set_status") as mock_set_status, \
             patch.object(controller, "git_sync") as mock_git:
            run(controller.handle_decision(update, make_context()))

        mock_get.assert_not_called()
        mock_set_status.assert_not_called()
        mock_git.sync_offers_db.assert_not_called()
        update.callback_query.edit_message_text.assert_not_awaited()
        # El callback siempre se responde a Telegram (query.answer()), aunque
        # se ignore el resto - evita el "cargando..." infinito en el botón.
        update.callback_query.answer.assert_awaited_once()


class TestMalformedCallbackData:
    def test_malformed_callback_data_is_logged_and_does_not_raise(self):
        update = make_update("approve-sin-separador")

        with patch.object(controller.db, "get_offer_by_rowid") as mock_get, \
             patch.object(controller.db, "set_status") as mock_set_status, \
             patch.object(controller, "git_sync") as mock_git, \
             patch.object(controller.logger, "error") as mock_log_error:
            run(controller.handle_decision(update, make_context()))

        mock_log_error.assert_called_once()
        mock_get.assert_not_called()
        mock_set_status.assert_not_called()
        mock_git.sync_offers_db.assert_not_called()
        update.callback_query.edit_message_text.assert_not_awaited()

    def test_non_integer_rowid_is_logged_and_does_not_raise(self):
        update = make_update("approve:no-es-un-numero")

        with patch.object(controller.db, "get_offer_by_rowid") as mock_get, \
             patch.object(controller.logger, "error") as mock_log_error:
            run(controller.handle_decision(update, make_context()))

        mock_log_error.assert_called_once()
        mock_get.assert_not_called()
