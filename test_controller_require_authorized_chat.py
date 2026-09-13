"""test_controller_require_authorized_chat.py - Pruebas del decorador
@require_authorized_chat en sí (controller.py), independientes de los 5
handlers reales que ya lo usan (start/status/seguimiento/handle_decision/
handle_close_contact, cubiertos indirectamente en test_controller_decision.py
y test_controller_seguimiento.py).

Se declaran dos handlers mínimos de prueba, uno para cada modo del
decorador (is_callback=False / is_callback=True), y se comprueba que:
- Con chat_id autorizado, el cuerpo del handler se ejecuta y su valor de
  retorno llega intacto a quien lo llama.
- Con chat_id no autorizado, el cuerpo NUNCA se ejecuta.
- En el modo callback, query.answer() se llama SIEMPRE (antes de comprobar
  la autorización), tanto si el chat está autorizado como si no.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import controller
from config import TELEGRAM_CHAT_ID


def run(coro):
    return asyncio.run(coro)


@controller.require_authorized_chat()
async def _dummy_message_handler(update, context):
    return "ejecutado"


@controller.require_authorized_chat(is_callback=True)
async def _dummy_callback_handler(update, context):
    return "ejecutado"


class TestRequireAuthorizedChatMessageMode:
    def test_authorized_chat_runs_handler_body(self):
        update = MagicMock()
        update.effective_chat.id = TELEGRAM_CHAT_ID

        result = run(_dummy_message_handler(update, MagicMock()))

        assert result == "ejecutado"

    def test_unauthorized_chat_skips_handler_body(self):
        update = MagicMock()
        update.effective_chat.id = -1

        with patch.object(controller.logger, "warning") as mock_warn:
            result = run(_dummy_message_handler(update, MagicMock()))

        assert result is None
        mock_warn.assert_called_once()


class TestRequireAuthorizedChatCallbackMode:
    def test_authorized_chat_answers_query_and_runs_handler_body(self):
        update = MagicMock()
        update.callback_query.answer = AsyncMock()
        update.callback_query.message.chat.id = TELEGRAM_CHAT_ID

        result = run(_dummy_callback_handler(update, MagicMock()))

        assert result == "ejecutado"
        update.callback_query.answer.assert_awaited_once()

    def test_unauthorized_chat_still_answers_query_but_skips_handler_body(self):
        update = MagicMock()
        update.callback_query.answer = AsyncMock()
        update.callback_query.message.chat.id = -1

        with patch.object(controller.logger, "warning") as mock_warn:
            result = run(_dummy_callback_handler(update, MagicMock()))

        assert result is None
        # El botón siempre se responde a Telegram aunque se ignore el resto -
        # evita el "cargando..." infinito, igual que en handle_decision/
        # handle_close_contact.
        update.callback_query.answer.assert_awaited_once()
        mock_warn.assert_called_once()
