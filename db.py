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
    """Crea la tabla `offers` si no existe. Idempotente."""
    with get_connection() as conn:
        conn.execute(SCHEMA)


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
