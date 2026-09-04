import logging
import threading
from flask import Flask
from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from config import TELEGRAM_BOT_TOKEN as TOKEN
import db

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
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    logger.info(f"📩 Recibido /start de {chat_id}")
    await update.message.reply_text(
        "🤖 <b>Bot de control del Agente FCT</b>\n\n"
        "Las búsquedas se ejecutan solas por cron (GitHub Actions). Este bot "
        "solo te avisa de ofertas nuevas y espera tu decisión con los botones "
        "✅ Aprobar / ❌ Descartar de cada mensaje.\n\n"
        "Comandos disponibles:\n"
        "/status - Ver estado del bot y ofertas pendientes",
        parse_mode="HTML"
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
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

def run_telegram_bot():
    logger.info("🤖 Iniciando bot de Telegram...")
    db.init_db()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CallbackQueryHandler(handle_decision))

    logger.info("🚀 Bot de control iniciado en Render.com...")
    logger.info("📱 Comandos disponibles: /start, /status")
    app.run_polling()

if __name__ == "__main__":
    web_thread = threading.Thread(target=lambda: app_web.run(host='0.0.0.0', port=10000, debug=False))
    web_thread.daemon = True
    web_thread.start()
    logger.info("🌐 Servidor web iniciado en puerto 10000")

    run_telegram_bot()
