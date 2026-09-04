"""migrate_to_sqlite.py - Script puntual: migra approved_offers.json a offers.db.

Se ejecuta una sola vez, a mano (`python migrate_to_sqlite.py`), como parte de
la Fase 3. No lo llama ningún otro módulo del proyecto.

Decisiones de la migración:

1. Las URLs de memory.json que NO están en approved_offers.json (vistas pero
   descartadas por el filtro de keywords antiguo, ya eliminado en la Fase 2)
   NO se migran a propósito. Ese filtro ya no existe: si esas ofertas vuelven
   a aparecer en una búsqueda, se re-clasifican desde cero con el criterio
   Tier A/B/descarte actual, en vez de arrastrar una decisión de un sistema
   que ya no usamos.

2. Los registros de approved_offers.json se migran con tier='legacy' y
   status='pending': el sistema antiguo los auto-aprobaba por puntuación de
   keywords (>=70), no por una decisión humana real ni por el criterio Tier
   A/B/descarte actual. Bajo el nuevo flujo (Fase 4) quedan pendientes de
   revisión de verdad.

3. approved_offers.json tiene entradas con mojibake: texto UTF-8 que en algún
   momento (versión antigua del código, antes de que main.py forzara
   encoding='utf-8' en todos los open()) se guardó como bytes Windows-1252
   sueltos en vez de UTF-8 multibyte. fix_mojibake() intenta recuperar el
   texto original registro a registro.
"""
import json
import logging

import db
from config import LEGACY_APPROVED_FILE

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

LEGACY_JUSTIFICATION = (
    "Migrado del sistema antiguo (auto-aprobado por puntuación de keywords, "
    "no por el criterio Tier A/B/descarte actual) - pendiente de revisión real."
)


def fix_mojibake(value: str) -> str:
    """Si el string contiene bytes que no eran UTF-8 válido (preservados como
    surrogados por errors='surrogateescape' al leer el archivo), intenta
    recuperar el texto original reinterpretando esos bytes como cp1252.
    Si el string ya era UTF-8 válido, lo devuelve sin tocar."""
    if not any(0xDC80 <= ord(ch) <= 0xDCFF for ch in value):
        return value
    raw = value.encode('utf-8', errors='surrogateescape')
    try:
        return raw.decode('cp1252')
    except UnicodeDecodeError:
        return raw.decode('cp1252', errors='replace')


def parse_concatenated_json(content: str):
    """approved_offers.json es una secuencia de objetos JSON con indent=2
    concatenados (cada uno escrito con model_dump_json(indent=2) + '\\n'),
    no un array JSON válido ni JSON-lines de una línea por objeto. Los separa
    usando raw_decode del decodificador estándar."""
    decoder = json.JSONDecoder()
    idx = 0
    content = content.strip()
    objects = []
    while idx < len(content):
        remainder = content[idx:].lstrip()
        if not remainder:
            break
        idx = len(content) - len(remainder)
        try:
            obj, end = decoder.raw_decode(content, idx)
        except json.JSONDecodeError as e:
            logger.error(f"Error decodificando JSON en posición {idx}: {e}")
            break
        objects.append(obj)
        idx = end
    return objects


def migrate():
    db.init_db()

    try:
        with open(LEGACY_APPROVED_FILE, 'r', encoding='utf-8', errors='surrogateescape') as f:
            content = f.read()
    except FileNotFoundError:
        logger.info(f"No existe {LEGACY_APPROVED_FILE}, nada que migrar.")
        return

    objects = parse_concatenated_json(content)
    logger.info(f"{len(objects)} registros encontrados en {LEGACY_APPROVED_FILE}")

    migrated = 0
    skipped = 0

    for obj in objects:
        url = obj.get('url', '')
        if not url or db.is_seen(url):
            skipped += 1
            continue

        with db.get_connection() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO offers
                   (url, title, company, location, mode, source, description,
                    tier, justification, status, found_date)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    url,
                    fix_mojibake(obj.get('title', '')),
                    fix_mojibake(obj.get('company', '')),
                    fix_mojibake(obj.get('location', '')),
                    obj.get('mode', ''),
                    obj.get('source', 'legacy'),
                    fix_mojibake(obj.get('description', '')),
                    'legacy',
                    LEGACY_JUSTIFICATION,
                    'pending',
                    obj.get('found_date', ''),
                )
            )
        migrated += 1

    logger.info(f"Migración completa: {migrated} migradas, {skipped} omitidas (ya existentes o sin URL).")


if __name__ == "__main__":
    migrate()
