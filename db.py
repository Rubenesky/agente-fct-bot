"""db.py - Almacenamiento compartido en SQLite (sustituye a memory.json y
approved_offers.json). Una sola tabla `offers` sirve de deduplicación
(¿ya vimos esta URL?) y de registro de decisiones humanas (pendiente/
aprobada/rechazada), para que tanto el workflow de búsqueda (GitHub Actions)
como el bot de Telegram (Render) lean y escriban el mismo estado.
"""
import sqlite3
from contextlib import contextmanager
from datetime import datetime

from config import DB_FILE

SCHEMA = """
CREATE TABLE IF NOT EXISTS offers (
    url TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    location TEXT NOT NULL,
    mode TEXT NOT NULL,
    source TEXT NOT NULL,
    description TEXT,
    tier TEXT NOT NULL,
    justification TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    found_date TEXT NOT NULL,
    decided_date TEXT
);
"""

# Tabla `contacts` - recordatorio de seguimiento a reclutadores/empresas con
# los que el estudiante ya habló. Usa el rowid implícito de SQLite como
# identificador corto, igual que `offers` (se reutiliza en los botones de
# Telegram vía callback_data).
#
# Qué NO conviene anotar en `notes`: este campo es texto libre y offers.db
# se versiona en git (ver README, "Decisiones de diseño"), así que cualquier
# cosa que se escriba aquí queda en el historial del repo para siempre,
# aunque luego se borre la fila con /olvidar o delete_contact_by_name. Evita
# guardar datos personales del reclutador más allá de lo estrictamente
# necesario para el seguimiento (nada de teléfono/email/DNI personal,
# comentarios sobre su aspecto o vida privada, capturas de conversaciones
# privadas, etc.) - un nombre y una nota breve sobre el proceso ("dijo que
# respondía la semana que viene") es el tipo de cosa para la que se pensó
# este campo.
CONTACTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS contacts (
    company TEXT NOT NULL,
    contact_name TEXT,
    channel TEXT,
    notes TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    next_followup_date TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_date TEXT NOT NULL,
    last_contact_date TEXT
);
"""


@contextmanager
def get_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    """Crea las tablas `offers` y `contacts` si no existen. Idempotente."""
    with get_connection() as conn:
        conn.execute(SCHEMA)
        conn.execute(CONTACTS_SCHEMA)


def is_seen(url: str) -> bool:
    """True si esta URL ya está en la base de datos (independientemente de
    su tier o estado) - se usa para no reprocesar/reclasificar la misma
    oferta en cada ejecución."""
    with get_connection() as conn:
        row = conn.execute("SELECT 1 FROM offers WHERE url = ?", (url,)).fetchone()
        return row is not None


def save_offer(offer) -> int:
    """Guarda una oferta ya clasificada (tier + justification ya asignados).

    status inicial: 'descarte' si tier == 'descarte' (no necesita revisión
    humana), 'pending' en cualquier otro caso (A, B, error) - pendiente de
    aprobación/rechazo por Telegram. No sobreescribe si la URL ya existe
    (INSERT OR IGNORE).

    Devuelve el rowid de la fila (nueva o ya existente) - se usa como
    identificador corto en los botones de Telegram, ya que una URL completa
    no cabe en el límite de 64 bytes de callback_data.
    """
    status = "descarte" if offer.tier == "descarte" else "pending"
    with get_connection() as conn:
        cursor = conn.execute(
            """INSERT OR IGNORE INTO offers
               (url, title, company, location, mode, source, description,
                tier, justification, status, found_date)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (offer.url, offer.title, offer.company, offer.location, offer.mode,
             offer.source, offer.description, offer.tier, offer.justification,
             status, offer.found_date)
        )
        if cursor.lastrowid and cursor.rowcount > 0:
            return cursor.lastrowid
        # La URL ya existía (no debería pasar en el flujo normal, ya que
        # search_node filtra con is_seen() antes de llegar aquí) - se
        # recupera el rowid existente en vez de devolver uno inválido.
        row = conn.execute("SELECT rowid FROM offers WHERE url = ?", (offer.url,)).fetchone()
        return row['rowid']


def get_offer_by_rowid(rowid: int) -> dict | None:
    """Busca una oferta por su rowid (identificador corto usado en los
    botones de Telegram)."""
    with get_connection() as conn:
        row = conn.execute("SELECT rowid, * FROM offers WHERE rowid = ?", (rowid,)).fetchone()
        return dict(row) if row else None


def get_pending_offers() -> list[dict]:
    """Ofertas Tier A/B/error aún sin decisión humana, más antiguas primero."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT rowid, * FROM offers WHERE status = 'pending' ORDER BY found_date"
        ).fetchall()
        return [dict(row) for row in rows]


def set_status(rowid: int, status: str) -> None:
    """Registra la decisión humana ('approved' o 'rejected') sobre una oferta,
    identificada por su rowid."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE offers SET status = ?, decided_date = ? WHERE rowid = ?",
            (status, datetime.now().isoformat(), rowid)
        )


def get_error_offers(limit: int) -> list[dict]:
    """Ofertas que quedaron con tier='error' (fallo de clasificación en una
    ejecución anterior, normalmente rate limit de Gemini) a reintentar, más
    antiguas primero. `limit` acota cuántas se devuelven para no saltarse el
    tope de clasificaciones por ejecución (MAX_CLASSIFICATIONS_PER_RUN)."""
    if limit <= 0:
        return []
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT rowid, * FROM offers WHERE tier = 'error' ORDER BY found_date LIMIT ?",
            (limit,)
        ).fetchall()
        return [dict(row) for row in rows]


def update_offer_classification(rowid: int, tier: str, justification: str) -> None:
    """Actualiza tier/justification de una oferta que ya existía en la base
    de datos (reintento de una que había quedado en 'error'), a diferencia de
    save_offer que solo inserta ofertas nuevas.

    status se deriva del nuevo tier igual que en save_offer: 'descarte' si
    tier == 'descarte', 'pending' en cualquier otro caso (sigue pendiente de
    revisión humana, incluso si vuelve a fallar y sigue en 'error')."""
    status = "descarte" if tier == "descarte" else "pending"
    with get_connection() as conn:
        conn.execute(
            "UPDATE offers SET tier = ?, justification = ?, status = ? WHERE rowid = ?",
            (tier, justification, status, rowid)
        )


def set_followup(company: str, next_followup_date: str, notes: str | None = None) -> int:
    """Registra (o reprograma) un seguimiento pendiente para una empresa.

    Busca una fila existente por nombre de empresa, sin distinguir
    mayúsculas/minúsculas. Si existe: incrementa attempt_count en 1,
    actualiza next_followup_date y last_contact_date (ahora), y conserva las
    notes anteriores si no se pasa un valor no vacío. Si no existe: inserta
    una fila nueva con attempt_count=1, status='active', created_date y
    last_contact_date=ahora.

    Devuelve el rowid de la fila (nueva o actualizada) - se usa como
    identificador corto en los botones de Telegram, igual que save_offer con
    las ofertas."""
    now = datetime.now().isoformat()
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT rowid FROM contacts WHERE LOWER(company) = LOWER(?)", (company,)
        ).fetchone()

        if existing is not None:
            rowid = existing["rowid"]
            if notes:
                conn.execute(
                    """UPDATE contacts
                       SET attempt_count = attempt_count + 1,
                           next_followup_date = ?,
                           last_contact_date = ?,
                           notes = ?
                       WHERE rowid = ?""",
                    (next_followup_date, now, notes, rowid)
                )
            else:
                conn.execute(
                    """UPDATE contacts
                       SET attempt_count = attempt_count + 1,
                           next_followup_date = ?,
                           last_contact_date = ?
                       WHERE rowid = ?""",
                    (next_followup_date, now, rowid)
                )
            return rowid

        cursor = conn.execute(
            """INSERT INTO contacts
               (company, notes, attempt_count, next_followup_date, status,
                created_date, last_contact_date)
               VALUES (?, ?, 1, ?, 'active', ?, ?)""",
            (company, notes, next_followup_date, now, now)
        )
        return cursor.lastrowid


def get_due_followups() -> list[dict]:
    """Contactos activos con seguimiento vencido (fecha de hoy o anterior),
    más antiguos primero. Se usa desde el cron (main.py) para decidir a
    quién avisar por Telegram en cada ejecución."""
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT rowid, * FROM contacts
               WHERE status = 'active'
                 AND next_followup_date IS NOT NULL
                 AND next_followup_date <= date('now')
               ORDER BY next_followup_date"""
        ).fetchall()
        return [dict(row) for row in rows]


def clear_followup_date(rowid: int) -> None:
    """Pone next_followup_date a NULL tras notificar un seguimiento vencido,
    para no repetir el mismo aviso en cada ejecución del cron hasta que el
    usuario reprograme una nueva fecha con /seguimiento."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE contacts SET next_followup_date = NULL WHERE rowid = ?", (rowid,)
        )


def close_contact(rowid: int) -> None:
    """Marca un contacto como cerrado (respondió, o el usuario deja de
    insistir) - deja de aparecer en get_due_followups."""
    with get_connection() as conn:
        conn.execute("UPDATE contacts SET status = 'closed' WHERE rowid = ?", (rowid,))


def delete_contact_by_name(company: str) -> int:
    """Borra de verdad (DELETE, no soft-delete) todas las filas de `contacts`
    cuyo `company` coincida sin distinguir mayúsculas/minúsculas, a
    diferencia de close_contact (que solo cambia status a 'closed' y deja la
    fila, con sus `notes`, en la base de datos indefinidamente).

    Se usa desde el comando /olvidar cuando el estudiante quiere que un
    contacto deje de existir de verdad (p.ej. si se anotó por error algo
    sensible en `notes`), no solo que deje de aparecer como pendiente.

    Devuelve cuántas filas se borraron (0 si no existía ninguna con ese
    nombre). En uso normal debería ser como mucho 1 (set_followup no
    duplica filas), pero si hubiera varias con el mismo nombre se borran
    todas."""
    with get_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM contacts WHERE LOWER(company) = LOWER(?)", (company,)
        )
        return cursor.rowcount
