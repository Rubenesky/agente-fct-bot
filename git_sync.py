"""git_sync.py - Sincroniza offers.db con git tras cada decisión del bot de
Telegram (aprobar/descartar).

Render no tiene disco persistente configurado (ver render.yaml), así que el
filesystem del servicio se resetea en cada redeploy: si el bot solo escribe
la decisión en el offers.db local (vía db.py), esa decisión se pierde. Este
módulo replica desde Python el mismo patrón de git que ya usa el paso
"Guardar offers.db actualizado" de .github/workflows/search.yml (add +
commit + pull --rebase + push), para que las decisiones del bot queden
también reflejadas en el repo remoto y no se pierdan ni sean sobreescritas
por el próximo commit automático del cron.
"""
import logging
import re
import subprocess
from pathlib import Path

from config import DB_FILE, GIT_PUSH_TOKEN

logger = logging.getLogger(__name__)

REPO_DIR = Path(__file__).resolve().parent

# Identidad de git a usar si el entorno de Render no tiene una configurada
# (equivalente a los `git config user.name/user.email` de search.yml).
GIT_USER_NAME = "agente-fct bot"
GIT_USER_EMAIL = "agente-fct-bot@users.noreply.github.com"

# Reconoce URLs de remote tipo https://github.com/OWNER/REPO(.git) o
# git@github.com:OWNER/REPO(.git), para reconstruirlas con el token embebido.
_REMOTE_URL_RE = re.compile(
    r"^(?:https://(?:[^@/]+@)?github\.com/|git@github\.com:)"
    r"(?P<owner>[^/]+)/(?P<repo>.+?)(?:\.git)?/?$"
)


def _run(args: list[str]) -> subprocess.CompletedProcess:
    """Ejecuta un comando git dentro del repo, capturando stdout/stderr."""
    return subprocess.run(
        args,
        cwd=REPO_DIR,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _scrub(text: str | None, token: str) -> str:
    """Elimina el token de un texto antes de loguearlo (por si aparece en un
    mensaje de error de git, p.ej. una URL de remote rechazada)."""
    if not text:
        return ""
    scrubbed = text.replace(token, "***")
    scrubbed = re.sub(r"x-access-token:[^@]*@", "x-access-token:***@", scrubbed)
    return scrubbed.strip()


def _authenticated_remote_url(token: str) -> str | None:
    """Lee la URL del remote 'origin' y la reescribe con el token como
    credencial (formato https://x-access-token:TOKEN@github.com/OWNER/REPO.git).

    Se pasa la URL directamente a `git pull`/`git push` en vez de escribirla
    con `git remote set-url`, para que el token nunca quede persistido en
    .git/config.
    """
    result = _run(["git", "remote", "get-url", "origin"])
    if result.returncode != 0:
        logger.error("git_sync: no se pudo leer el remote 'origin': %s", _scrub(result.stderr, token))
        return None

    match = _REMOTE_URL_RE.match(result.stdout.strip())
    if not match:
        logger.error("git_sync: formato de URL de remote 'origin' no reconocido")
        return None

    owner, repo = match.group("owner"), match.group("repo")
    return f"https://x-access-token:{token}@github.com/{owner}/{repo}.git"


def _ensure_git_identity() -> None:
    _run(["git", "config", "user.name", GIT_USER_NAME])
    _run(["git", "config", "user.email", GIT_USER_EMAIL])


def sync_offers_db(reason: str) -> bool:
    """Sube offers.db a git tras un cambio de estado desde Telegram.

    Sigue el mismo patrón que .github/workflows/search.yml: add -> commit
    (si hay cambios) -> pull --rebase -> push. Nunca propaga una excepción:
    cualquier fallo se loguea con logger.error y devuelve False, para no
    romper el flujo del bot ni bloquear la respuesta a Telegram. Si falla,
    el siguiente intento (la próxima decisión, o el pull --rebase del cron
    de GitHub Actions) puede recuperarlo - no hay reintentos automáticos.
    """
    token = GIT_PUSH_TOKEN
    if not token:
        logger.warning(
            "git_sync: GIT_PUSH_TOKEN no configurado, se omite la sincronización "
            "de %s con git (la decisión solo queda en el disco local, que en "
            "Render no es persistente entre redeploys).",
            DB_FILE,
        )
        return False

    try:
        remote_url = _authenticated_remote_url(token)
        if remote_url is None:
            return False

        _ensure_git_identity()

        add_result = _run(["git", "add", DB_FILE])
        if add_result.returncode != 0:
            logger.error("git_sync: 'git add' falló: %s", _scrub(add_result.stderr, token))
            return False

        # `git diff --cached --quiet` devuelve 0 si NO hay cambios staged.
        diff_result = _run(["git", "diff", "--cached", "--quiet", "--", DB_FILE])
        if diff_result.returncode == 0:
            logger.info("git_sync: sin cambios que sincronizar en %s", DB_FILE)
            return True

        commit_result = _run(["git", "commit", "-m", f"Actualiza offers.db ({reason}) [skip ci]"])
        if commit_result.returncode != 0:
            logger.error("git_sync: 'git commit' falló: %s", _scrub(commit_result.stderr, token))
            return False

        branch_result = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
        branch = branch_result.stdout.strip() if branch_result.returncode == 0 else "main"

        pull_result = _run(["git", "pull", "--rebase", remote_url, branch])
        if pull_result.returncode != 0:
            logger.error(
                "git_sync: 'git pull --rebase' falló (posible carrera con otro "
                "push casi simultáneo, p.ej. el cron): %s",
                _scrub(pull_result.stderr, token),
            )
            # Deja el repo limpio para el próximo intento en vez de a medio rebasear.
            _run(["git", "rebase", "--abort"])
            return False

        push_result = _run(["git", "push", remote_url, f"HEAD:{branch}"])
        if push_result.returncode != 0:
            logger.error("git_sync: 'git push' falló: %s", _scrub(push_result.stderr, token))
            return False

        logger.info("git_sync: %s sincronizado con git (%s)", DB_FILE, reason)
        return True

    except subprocess.TimeoutExpired:
        # No se usa str(e): TimeoutExpired incluye el comando completo (con
        # el token embebido en la URL de pull/push) en su representación.
        logger.error("git_sync: timeout ejecutando un comando git")
        return False
    except Exception:
        logger.exception("git_sync: error inesperado sincronizando %s con git", DB_FILE)
        return False
