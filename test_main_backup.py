"""test_main_backup.py - Pruebas del backup de offers.db por Telegram.

offers.db (SQLite) es la única fuente de verdad de la app y solo está
versionada en git: backup_offers_db_to_telegram() envía el propio fichero
como documento adjunto por Telegram (sendDocument, multipart/form-data) al
chat configurado en TELEGRAM_CHAT_ID, dando una copia fuera de git.

run_agent() llama a esta función solo los domingos (datetime.now().weekday()
== 6) para no generar ruido en el chat en cada ejecución del cron (2/día).

No se llama a la API real de Telegram: se mockea requests.post. Para
comprobar la cadencia se mockea main.datetime.
"""
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

import main


SUNDAY = datetime(2024, 1, 7, 9, 0, 0)      # domingo
MONDAY = datetime(2024, 1, 8, 9, 0, 0)      # lunes


@pytest.fixture
def temp_db_file(tmp_path):
    db_file = tmp_path / "test_offers.db"
    db_file.write_bytes(b"contenido falso de sqlite")
    with patch.object(main, "DB_FILE", str(db_file)):
        yield str(db_file)


class TestBackupOffersDbToTelegram:
    def test_sends_document_with_correct_file_and_caption(self, temp_db_file):
        with patch.object(main.requests, "post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            result = main.backup_offers_db_to_telegram()

        assert result is True
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert args[0] == f"https://api.telegram.org/bot{main.TELEGRAM_BOT_TOKEN}/sendDocument"
        assert kwargs["data"]["chat_id"] == main.TELEGRAM_CHAT_ID
        assert "caption" in kwargs["data"]
        # El fichero adjunto debe ser el de DB_FILE (config), no un nombre
        # hardcodeado.
        assert kwargs["files"]["document"][0] == temp_db_file

    def test_caption_includes_date_and_time(self, temp_db_file):
        with patch.object(main.requests, "post") as mock_post, \
             patch.object(main, "datetime") as mock_datetime:
            mock_datetime.now.return_value = SUNDAY
            mock_post.return_value = MagicMock(status_code=200)
            main.backup_offers_db_to_telegram()

        caption = mock_post.call_args.kwargs["data"]["caption"]
        assert "07/01/2024" in caption

    def test_returns_false_on_non_200_response(self, temp_db_file):
        with patch.object(main.requests, "post") as mock_post:
            mock_post.return_value = MagicMock(status_code=500)
            result = main.backup_offers_db_to_telegram()

        assert result is False

    def test_returns_false_and_does_not_raise_on_network_error(self, temp_db_file):
        with patch.object(main.requests, "post", side_effect=Exception("boom de red")):
            result = main.backup_offers_db_to_telegram()

        assert result is False


class TestRunAgentBackupCadence:
    def test_calls_backup_on_sunday(self):
        fake_app = MagicMock()
        fake_app.invoke.return_value = {"offers": []}

        with patch.object(main.db, "init_db"), \
             patch.object(main, "create_graph", return_value=fake_app), \
             patch.object(main, "notify_due_followups"), \
             patch.object(main, "datetime") as mock_datetime, \
             patch.object(main, "backup_offers_db_to_telegram") as mock_backup:
            mock_datetime.now.return_value = SUNDAY
            main.run_agent()

        mock_backup.assert_called_once()

    def test_does_not_call_backup_on_monday(self):
        fake_app = MagicMock()
        fake_app.invoke.return_value = {"offers": []}

        with patch.object(main.db, "init_db"), \
             patch.object(main, "create_graph", return_value=fake_app), \
             patch.object(main, "notify_due_followups"), \
             patch.object(main, "datetime") as mock_datetime, \
             patch.object(main, "backup_offers_db_to_telegram") as mock_backup:
            mock_datetime.now.return_value = MONDAY
            main.run_agent()

        mock_backup.assert_not_called()

    def test_run_agent_survives_backup_raising_exception(self):
        fake_app = MagicMock()
        fake_result = {"offers": []}
        fake_app.invoke.return_value = fake_result

        with patch.object(main.db, "init_db"), \
             patch.object(main, "create_graph", return_value=fake_app), \
             patch.object(main, "notify_due_followups"), \
             patch.object(main, "datetime") as mock_datetime, \
             patch.object(main, "backup_offers_db_to_telegram", side_effect=Exception("boom")), \
             patch.object(main, "send_telegram") as mock_send_telegram:
            mock_datetime.now.return_value = SUNDAY
            result = main.run_agent()

        # La ejecución no debe marcarse como fallida ni mandar el aviso de
        # error general: el backup falló, pero la búsqueda/clasificación
        # (fake_app.invoke) ya se completó bien.
        assert result is fake_result
        mock_send_telegram.assert_not_called()

    def test_run_agent_survives_backup_returning_false(self):
        fake_app = MagicMock()
        fake_result = {"offers": []}
        fake_app.invoke.return_value = fake_result

        with patch.object(main.db, "init_db"), \
             patch.object(main, "create_graph", return_value=fake_app), \
             patch.object(main, "notify_due_followups"), \
             patch.object(main, "datetime") as mock_datetime, \
             patch.object(main, "backup_offers_db_to_telegram", return_value=False) as mock_backup, \
             patch.object(main, "send_telegram") as mock_send_telegram:
            mock_datetime.now.return_value = SUNDAY
            result = main.run_agent()

        assert result is fake_result
        mock_backup.assert_called_once()
        mock_send_telegram.assert_not_called()
