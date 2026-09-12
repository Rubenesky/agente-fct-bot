"""test_git_sync.py - Pruebas de git_sync.py: sincronización de offers.db
con git tras cada decisión del bot de Telegram (add -> commit -> pull
--rebase -> push), el fallback de owner/repo cuando no hay remote 'origin',
el scrubbing del token en los logs, y la nueva alerta por Telegram cuando
sync_offers_db lleva varios fallos consecutivos.

No se ejecuta git de verdad: se mockea subprocess.run. Tampoco se llama a
la API real de Telegram: se mockea requests.post.
"""
from unittest.mock import MagicMock, patch

import pytest

import git_sync

FAKE_TOKEN = "fake-git-push-token-123"


def _proc(returncode=0, stdout="", stderr=""):
    return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)


def _step_for(args):
    """Identifica a qué paso del flujo corresponde una llamada a subprocess.run
    a partir del comando git invocado (args[0] == 'git')."""
    sub = args[1]
    if sub == "remote":
        return "remote"
    if sub == "config":
        return "config"
    if sub == "add":
        return "add"
    if sub == "diff":
        return "diff"
    if sub == "commit":
        return "commit"
    if sub == "pull":
        return "pull"
    if sub == "push":
        return "push"
    if sub == "status":
        return "status"
    if sub == "rebase":
        return "rebase_abort"
    return "unknown"


def make_run_side_effect(remote_url="https://github.com/OWNER/REPO.git", **overrides):
    """Construye un side_effect para subprocess.run que simula un flujo de
    git_sync completo. Por defecto todos los pasos tienen éxito y hay
    cambios que sincronizar (diff devuelve returncode=1). `overrides` permite
    forzar el resultado (returncode/stdout/stderr) de un paso concreto, p.ej.
    make_run_side_effect(add=_proc(returncode=1, stderr="fallo")).
    """
    defaults = {
        "remote": _proc(returncode=0, stdout=f"{remote_url}\n"),
        "config": _proc(returncode=0),
        "add": _proc(returncode=0),
        "diff": _proc(returncode=1),  # != 0 => hay cambios que commitear
        "commit": _proc(returncode=0),
        "pull": _proc(returncode=0),
        "push": _proc(returncode=0),
        "status": _proc(returncode=0, stdout=""),
        "rebase_abort": _proc(returncode=0),
    }
    defaults.update(overrides)

    def side_effect(args, **kwargs):
        step = _step_for(args)
        return defaults.get(step, _proc(returncode=0))

    return side_effect


@pytest.fixture(autouse=True)
def reset_failure_counter():
    """El contador de fallos consecutivos es una variable de módulo (igual
    que en producción, donde controller.py corre como un único proceso
    persistente): hay que resetearlo entre tests para que no se filtren
    fallos de un test a otro."""
    git_sync._consecutive_failures = 0
    yield
    git_sync._consecutive_failures = 0


@pytest.fixture(autouse=True)
def fake_token():
    with patch.object(git_sync, "GIT_PUSH_TOKEN", FAKE_TOKEN):
        yield FAKE_TOKEN


class TestHappyPath:
    def test_add_commit_pull_push_success_returns_true(self):
        with patch("git_sync.subprocess.run", side_effect=make_run_side_effect()) as mock_run:
            result = git_sync.sync_offers_db("aprobación oferta 1")

        assert result is True
        steps = [_step_for(call.args[0]) for call in mock_run.call_args_list]
        assert "add" in steps
        assert "commit" in steps
        assert "pull" in steps
        assert "push" in steps

    def test_no_changes_to_sync_returns_true_without_commit(self):
        # diff --cached --quiet devuelve 0 cuando NO hay cambios staged.
        with patch(
            "git_sync.subprocess.run",
            side_effect=make_run_side_effect(diff=_proc(returncode=0)),
        ) as mock_run:
            result = git_sync.sync_offers_db("sin cambios")

        assert result is True
        steps = [_step_for(call.args[0]) for call in mock_run.call_args_list]
        assert "commit" not in steps
        assert "pull" not in steps
        assert "push" not in steps


class TestIndividualStepFailures:
    def test_remote_url_unrecognized_format_returns_false_and_stops(self):
        with patch(
            "git_sync.subprocess.run",
            side_effect=make_run_side_effect(
                remote_url="not-a-valid-remote-url"
            ),
        ) as mock_run:
            result = git_sync.sync_offers_db("motivo")

        assert result is False
        steps = [_step_for(call.args[0]) for call in mock_run.call_args_list]
        assert "add" not in steps
        assert "commit" not in steps
        assert "pull" not in steps
        assert "push" not in steps

    def test_add_failure_returns_false_and_stops(self):
        with patch(
            "git_sync.subprocess.run",
            side_effect=make_run_side_effect(add=_proc(returncode=1, stderr="add falló")),
        ) as mock_run:
            result = git_sync.sync_offers_db("motivo")

        assert result is False
        steps = [_step_for(call.args[0]) for call in mock_run.call_args_list]
        assert "diff" not in steps
        assert "commit" not in steps
        assert "pull" not in steps
        assert "push" not in steps

    def test_commit_failure_returns_false_and_stops(self):
        with patch(
            "git_sync.subprocess.run",
            side_effect=make_run_side_effect(commit=_proc(returncode=1, stderr="commit falló")),
        ) as mock_run:
            result = git_sync.sync_offers_db("motivo")

        assert result is False
        steps = [_step_for(call.args[0]) for call in mock_run.call_args_list]
        assert "pull" not in steps
        assert "push" not in steps

    def test_pull_rebase_failure_returns_false_aborts_rebase_and_stops(self):
        with patch(
            "git_sync.subprocess.run",
            side_effect=make_run_side_effect(pull=_proc(returncode=1, stderr="pull falló")),
        ) as mock_run:
            result = git_sync.sync_offers_db("motivo")

        assert result is False
        steps = [_step_for(call.args[0]) for call in mock_run.call_args_list]
        assert "rebase_abort" in steps
        assert "push" not in steps

    def test_push_failure_returns_false(self):
        with patch(
            "git_sync.subprocess.run",
            side_effect=make_run_side_effect(push=_proc(returncode=1, stderr="push falló")),
        ):
            result = git_sync.sync_offers_db("motivo")

        assert result is False

    def test_timeout_returns_false(self):
        import subprocess as real_subprocess

        with patch(
            "git_sync.subprocess.run",
            side_effect=real_subprocess.TimeoutExpired(cmd=["git", "push"], timeout=30),
        ):
            result = git_sync.sync_offers_db("motivo")

        assert result is False

    def test_unexpected_exception_returns_false(self):
        with patch("git_sync.subprocess.run", side_effect=RuntimeError("boom")):
            result = git_sync.sync_offers_db("motivo")

        assert result is False


class TestOwnerRepoFallback:
    def test_falls_back_to_known_repo_when_no_origin_remote(self):
        with patch(
            "git_sync.subprocess.run",
            side_effect=make_run_side_effect(
                remote=_proc(returncode=1, stderr="No such remote 'origin'")
            ),
        ) as mock_run:
            result = git_sync.sync_offers_db("motivo")

        assert result is True
        push_call = next(
            call for call in mock_run.call_args_list if _step_for(call.args[0]) == "push"
        )
        pushed_url = push_call.args[0][2]
        assert "Rubenesky/agente-fct-bot" in pushed_url
        assert FAKE_TOKEN in pushed_url


class TestBranchIsAlwaysMain:
    def test_never_uses_head_as_branch(self):
        with patch("git_sync.subprocess.run", side_effect=make_run_side_effect()) as mock_run:
            git_sync.sync_offers_db("motivo")

        pull_call = next(
            call for call in mock_run.call_args_list if _step_for(call.args[0]) == "pull"
        )
        push_call = next(
            call for call in mock_run.call_args_list if _step_for(call.args[0]) == "push"
        )
        # git pull --rebase --autostash <remote_url> <branch>
        assert pull_call.args[0][-1] == "main"
        # git push <remote_url> HEAD:<branch>
        assert push_call.args[0][-1] == "HEAD:main"
        assert push_call.args[0][-1] != "HEAD:HEAD"


class TestTokenScrubbing:
    def test_token_never_appears_in_logger_calls(self):
        malicious_stderr = (
            f"remote: rejected, bad credentials for token {FAKE_TOKEN} "
            f"url https://x-access-token:{FAKE_TOKEN}@github.com/owner/repo.git"
        )
        with patch.object(git_sync, "logger") as mock_logger:
            with patch(
                "git_sync.subprocess.run",
                side_effect=make_run_side_effect(
                    commit=_proc(returncode=1, stderr=malicious_stderr)
                ),
            ):
                git_sync.sync_offers_db("motivo")

        all_calls = mock_logger.error.call_args_list + mock_logger.warning.call_args_list
        for call in all_calls:
            for arg in list(call.args) + list(call.kwargs.values()):
                text = str(arg)
                assert FAKE_TOKEN not in text
                assert f"x-access-token:{FAKE_TOKEN}@" not in text

    def test_pull_failure_scrubs_token_from_status_output_too(self):
        malicious_status = f"M offers.db (token leaked: {FAKE_TOKEN})"
        with patch.object(git_sync, "logger") as mock_logger:
            with patch(
                "git_sync.subprocess.run",
                side_effect=make_run_side_effect(
                    pull=_proc(returncode=1, stderr="pull falló"),
                    status=_proc(returncode=0, stdout=malicious_status),
                ),
            ):
                git_sync.sync_offers_db("motivo")

        all_calls = mock_logger.error.call_args_list + mock_logger.warning.call_args_list
        for call in all_calls:
            for arg in list(call.args) + list(call.kwargs.values()):
                assert FAKE_TOKEN not in str(arg)


class TestConsecutiveFailureAlert:
    def _fail_once(self):
        with patch(
            "git_sync.subprocess.run",
            side_effect=make_run_side_effect(add=_proc(returncode=1, stderr="add falló")),
        ):
            return git_sync.sync_offers_db("motivo")

    def _succeed_once(self):
        with patch("git_sync.subprocess.run", side_effect=make_run_side_effect()):
            return git_sync.sync_offers_db("motivo")

    def test_three_consecutive_failures_trigger_alert_exactly_once(self):
        with patch.object(git_sync, "requests") as mock_requests:
            mock_requests.post.return_value = MagicMock(status_code=200)
            assert self._fail_once() is False
            mock_requests.post.assert_not_called()
            assert self._fail_once() is False
            mock_requests.post.assert_not_called()
            assert self._fail_once() is False
            mock_requests.post.assert_called_once()

        sent_text = mock_requests.post.call_args.kwargs["json"]["text"]
        assert "3 fallos" in sent_text
        assert "GIT_PUSH_TOKEN" in sent_text

    def test_success_in_between_resets_counter_no_alert(self):
        with patch.object(git_sync, "requests") as mock_requests:
            mock_requests.post.return_value = MagicMock(status_code=200)
            assert self._fail_once() is False
            assert self._fail_once() is False
            assert self._succeed_once() is True
            assert self._fail_once() is False
            assert self._fail_once() is False

            mock_requests.post.assert_not_called()

    def test_alert_repeats_at_sixth_failure_but_not_fourth_or_fifth(self):
        with patch.object(git_sync, "requests") as mock_requests:
            mock_requests.post.return_value = MagicMock(status_code=200)
            for _ in range(3):
                self._fail_once()
            mock_requests.post.assert_called_once()

            self._fail_once()  # 4
            mock_requests.post.assert_called_once()

            self._fail_once()  # 5
            mock_requests.post.assert_called_once()

            self._fail_once()  # 6
            assert mock_requests.post.call_count == 2

        second_text = mock_requests.post.call_args.kwargs["json"]["text"]
        assert "6 fallos" in second_text

    def test_alert_failure_itself_is_swallowed(self):
        with patch.object(git_sync, "requests") as mock_requests:
            mock_requests.post.side_effect = RuntimeError("Telegram también caído")
            # No debe propagar la excepción aunque el propio aviso falle.
            assert self._fail_once() is False
            assert self._fail_once() is False
            assert self._fail_once() is False  # dispara el aviso, que falla internamente

    def test_token_not_configured_does_not_count_towards_alert(self):
        # El caso "GIT_PUSH_TOKEN no configurado" no es un fallo transitorio
        # de sincronización: no debe hacer avanzar el contador de alertas.
        with patch.object(git_sync, "GIT_PUSH_TOKEN", None), \
             patch.object(git_sync, "requests") as mock_requests:
            for _ in range(5):
                assert git_sync.sync_offers_db("motivo") is False

            mock_requests.post.assert_not_called()
        assert git_sync._consecutive_failures == 0
