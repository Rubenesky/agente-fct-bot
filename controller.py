import logging
import threading
import time
from datetime import datetime
from flask import Flask
from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from config import TELEGRAM_BOT_TOKEN as TOKEN, TELEGRAM_CHAT_ID
import db
import git_sync

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app_web = Flask(__name__)

@app_web.route('/')
def index():
    return "Bot de Telegram activo! 🚀"

@app_web.route('/health')
def health():
    return "OK", 200

# --- Funciones del bot ---
def _is_authorized(chat_id) -> bool:
    """Comprueba que el chat_id del mensaje coincide con el usuario autorizado del bot."""
    return str(chat_id) == str(TELEGRAM_CHAT_ID)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not _is_authorized(chat_id):
        logger.warning(f"⛔ /start ignorado: chat_id no autorizado ({chat_id})")
        return
    logger.info(f"📩 Recibido /start de {chat_id}")
    await update.message.reply_text(
        "🤖 <b>Bot de control del Agente FCT</b>\n\n"
        "Las búsquedas se ejecutan solas por cron (GitHub Actions). Este bot "
        "solo te avisa de ofertas nuevas y espera tu decisión con los botones "
        "✅ Aprobar / ❌ Descartar de cada mensaje.\n\n"
        "Comandos disponibles:\n"
        "/status - Ver estado del bot y ofertas pendientes\n"
        "/seguimiento Empresa X | 17/09/2026 | nota opcional - Programa un "
        "recordatorio de seguimiento",
        parse_mode="HTML"
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not _is_authorized(chat_id):
        logger.warning(f"⛔ /status ignorado: chat_id no autorizado ({chat_id})")
        return
    logger.info(f"📩 Recibido /status de {chat_id}")
    db.init_db()
    pending = db.get_pending_offers()
    await update.message.reply_text(
        "📊 <b>Estado del agente</b>\n\n"
        "✅ Bot activo en la nube (Render)\n"
        "🗂️ Base de datos: offers.db\n"
        f"⏳ Ofertas pendientes de revisión: {len(pending)}",
        parse_mode="HTML"
    )

async def handle_decision(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja los botones ✅ Aprobar / ❌ Descartar de cada oferta."""
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat.id if query.message else None
    if not _is_authorized(chat_id):
        logger.warning(f"⛔ Callback ignorado: chat_id no autorizado ({chat_id})")
        return

    try:
        action, rowid_str = query.data.split(":", 1)
        rowid = int(rowid_str)
    except (ValueError, AttributeError):
        logger.error(f"callback_data inesperado: {query.data!r}")
        return

    offer = db.get_offer_by_rowid(rowid)
    if offer is None:
        await query.edit_message_text(
            text=query.message.text_html + "\n\n⚠️ Oferta no encontrada en la base de datos.",
            parse_mode="HTML"
        )
        return

    status = "approved" if action == "approve" else "rejected"
    db.set_status(rowid, status)

    label = "✅ APROBADA" if status == "approved" else "❌ DESCARTADA"
    logger.info(f"{label}: {offer['title'][:50]} (rowid={rowid})")

    await query.edit_message_text(
        text=query.message.text_html + f"\n\n<b>{label}</b>",
        parse_mode="HTML"
    )

    # Sube offers.db a git para que la decisión no se pierda en el próximo
    # redeploy de Render (filesystem efímero) ni sea sobreescrita por el
    # próximo commit automático del cron de GitHub Actions. Se ejecuta
    # después de responder en Telegram (arriba) para que un push lento o
    # fallido nunca retrase ni rompa la respuesta al usuario - ver
    # git_sync.sync_offers_db, que ya captura cualquier error internamente.
    try:
        git_sync.sync_offers_db(reason=f"Telegram: {label} rowid={rowid}")
    except Exception:
        logger.exception(f"Fallo inesperado sincronizando offers.db con git (rowid={rowid})")

SEGUIMIENTO_HELP = (
    "ℹ️ Uso: /seguimiento Empresa X | 17/09/2026 | nota opcional\n"
    "(la nota es opcional; la fecha debe tener formato dd/mm/aaaa)"
)

def _parse_ddmmyyyy(value: str):
    """Convierte 'dd/mm/aaaa' a un date, o None si el formato no es válido."""
    try:
        return datetime.strptime(value, "%d/%m/%Y").date()
    except ValueError:
        return None

async def seguimiento(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /seguimiento Empresa X | dd/mm/aaaa | nota opcional - programa
    (o reprograma) un recordatorio de seguimiento con una empresa/contacto."""
    chat_id = update.effective_chat.id
    if not _is_authorized(chat_id):
        logger.warning(f"⛔ /seguimiento ignorado: chat_id no autorizado ({chat_id})")
        return
    logger.info(f"📩 Recibido /seguimiento de {chat_id}")

    text = update.message.text or ""
    body = text[len("/seguimiento"):].strip()
    # Quita también el sufijo "@nombre_del_bot" si el comando se escribió así.
    if body.startswith("@"):
        body = body.split(None, 1)[1] if " " in body else ""

    parts = [p.strip() for p in body.split("|")]
    empresa = parts[0] if parts else ""
    fecha_raw = parts[1] if len(parts) >= 2 else ""
    fecha = _parse_ddmmyyyy(fecha_raw) if fecha_raw else None

    if len(parts) < 2 or not empresa or fecha is None:
        await update.message.reply_text(SEGUIMIENTO_HELP)
        return

    nota = parts[2] if len(parts) >= 3 and parts[2] else None
    fecha_iso = fecha.isoformat()

    rowid = db.set_followup(empresa, fecha_iso, nota)

    # attempt_count no lo devuelve set_followup (solo el rowid, igual que
    # save_offer con las ofertas) - se consulta aparte para el mensaje de
    # confirmación, reutilizando get_connection() como ya hace el resto de
    # db.py.
    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT attempt_count FROM contacts WHERE rowid = ?", (rowid,)
        ).fetchone()
    attempt_count = row["attempt_count"] if row else 1

    logger.info(f"🔁 Seguimiento registrado: {empresa} -> {fecha_iso} (rowid={rowid})")

    await update.message.reply_text(
        f"✅ Seguimiento registrado para {empresa} — próximo recordatorio: "
        f"{fecha.strftime('%d/%m/%Y')} (intento nº {attempt_count})"
    )

    try:
        git_sync.sync_offers_db(reason=f"Telegram: /seguimiento {empresa}")
    except Exception:
        logger.exception(f"Fallo inesperado sincronizando offers.db con git (seguimiento: {empresa})")

async def handle_close_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja el botón "❌ Cerrar" de los avisos de seguimiento. Handler
    separado de handle_decision (que sigue siendo solo para approve/reject de
    ofertas) porque la lógica y la tabla afectada son distintas."""
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat.id if query.message else None
    if not _is_authorized(chat_id):
        logger.warning(f"⛔ Callback ignorado: chat_id no autorizado ({chat_id})")
        return

    try:
        _, rowid_str = query.data.split(":", 1)
        rowid = int(rowid_str)
    except (ValueError, AttributeError):
        logger.error(f"callback_data inesperado: {query.data!r}")
        return

    db.close_contact(rowid)
    logger.info(f"❌ Contacto cerrado (rowid={rowid})")

    await query.edit_message_text(
        text=query.message.text_html + "\n\n❌ Contacto cerrado",
        parse_mode="HTML"
    )

    try:
        git_sync.sync_offers_db(reason=f"Telegram: cierre contacto rowid={rowid}")
    except Exception:
        logger.exception(f"Fallo inesperado sincronizando offers.db con git (rowid={rowid})")

def run_telegram_bot():
    """Arranca el polling de Telegram con reintento en bucle: si otra
    instancia solapada (p.ej. durante un redeploy en Render) provoca un
    telegram.error.Conflict, run_polling() detiene toda la Application en
    vez de reintentar sola. Antes eso hacía que el proceso terminara y
    dependiera de que Render reiniciara el contenedor entero - si el
    contenedor viejo tardaba en morir, el nuevo volvía a chocar con él y
    el ciclo podía alargarse varios minutos. Ahora se reintenta dentro del
    mismo proceso tras una breve espera, sin depender de un reinicio de
    Render."""
    backoff_seconds = 10
    while True:
        try:
            logger.info("🤖 Iniciando bot de Telegram...")
            db.init_db()
            app = Application.builder().token(TOKEN).build()
            app.add_handler(CommandHandler("start", start))
            app.add_handler(CommandHandler("status", status))
            app.add_handler(CommandHandler("seguimiento", seguimiento))
            # handle_decision se acota a approve/reject de ofertas para que
            # no capture también los callbacks close_contact: de los avisos
            # de seguimiento (registrados aparte, con su propio handler).
            app.add_handler(CallbackQueryHandler(handle_decision, pattern="^(approve|reject):"))
            app.add_handler(CallbackQueryHandler(handle_close_contact, pattern="^close_contact:"))

            logger.info("🚀 Bot de control iniciado en Render.com...")
            logger.info("📱 Comandos disponibles: /start, /status, /seguimiento")
            app.run_polling()
            logger.warning(
                "El polling de Telegram terminó por su cuenta (posible Conflict "
                "con otra instancia) - reintentando en %ss", backoff_seconds
            )
        except Exception:
            logger.exception(
                "Fallo en el polling de Telegram - reintentando en %ss", backoff_seconds
            )
        time.sleep(backoff_seconds)

if __name__ == "__main__":
    web_thread = threading.Thread(target=lambda: app_web.run(host='0.0.0.0', port=10000, debug=False))
    web_thread.daemon = True
    web_thread.start()
    logger.info("🌐 Servidor web iniciado en puerto 10000")

    run_telegram_bot()
