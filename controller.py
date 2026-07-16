import subprocess
import asyncio
import threading
import logging
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# ============================================
# CONFIGURACIÓN
# ============================================
TOKEN = "8689965407:AAEAsajcfXecj0a3qTm-ivdFVc0yZ2B_QQg"

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
async def run_agent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    logger.info(f"📩 Recibido /run_agent de {chat_id}")
    await update.message.reply_text("✅ Comando recibido. Ejecutando agente...")
    
    try:
        logger.info("🚀 Ejecutando main.py once...")
        process = await asyncio.create_subprocess_exec(
            "python", "main.py", "once",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=600)
        stdout_text = stdout.decode('utf-8', errors='ignore')
        stderr_text = stderr.decode('utf-8', errors='ignore')
        
        logger.info("✅ main.py finalizado")
        
        msg = "🤖 <b>Agente ejecutado</b>\n"
        msg += f"📅 {__import__('time').strftime('%d/%m/%Y %H:%M:%S')}\n\n"
        
        if "--- OFERTAS NUEVAS ENCONTRADAS ---" in stdout_text:
            parts = stdout_text.split("--- OFERTAS NUEVAS ENCONTRADAS ---")
            if len(parts) > 1:
                offers_part = parts[1].strip()
                if offers_part:
                    msg += "📊 <b>Ofertas encontradas</b>\n"
                    msg += offers_part
        else:
            if "No se encontraron ofertas nuevas" in stdout_text:
                msg += "📊 No se encontraron ofertas nuevas en esta ejecución.\n"
            else:
                msg += "📊 No se encontraron ofertas nuevas en esta ejecución.\n"
        
        if stderr_text:
            error_lines = [line for line in stderr_text.split('\n') if "ERROR" in line]
            if error_lines:
                msg += f"\n⚠️ <b>Errores:</b>\n" + "\n".join(error_lines[:3])
        
        logger.info("📨 Enviando mensaje de resultado...")
        await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode="HTML")
        logger.info("📨 Mensaje enviado correctamente")
        
    except asyncio.TimeoutError:
        logger.error("⏰ Timeout en main.py")
        await context.bot.send_message(chat_id=chat_id, text="⏰ El agente tardó demasiado (>10 minutos)")
    except Exception as e:
        logger.error(f"❌ Error: {e}")
        await context.bot.send_message(chat_id=chat_id, text=f"❌ Error: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    logger.info(f"📩 Recibido /start de {chat_id}")
    await update.message.reply_text(
        "🤖 <b>Bot de control del Agente FCT</b>\n\n"
        "Comandos disponibles:\n"
        "/run_agent - Ejecutar la búsqueda de ofertas\n"
        "/status - Ver estado del agente",
        parse_mode="HTML"
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    logger.info(f"📩 Recibido /status de {chat_id}")
    await update.message.reply_text(
        "📊 <b>Estado del agente</b>\n\n"
        "✅ Bot activo en la nube (Render)\n"
        "📁 Archivos: memory.json, approved_offers.json, agente.log\n"
        "📱 Envía /run_agent para ejecutar manualmente",
        parse_mode="HTML"
    )

def run_telegram_bot():
    logger.info("🤖 Iniciando bot de Telegram...")
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("run_agent", run_agent))
    app.add_handler(CommandHandler("status", status))
    
    logger.info("🚀 Bot de control iniciado en Render.com...")
    logger.info("📱 Comandos disponibles: /start, /run_agent, /status")
    app.run_polling()

if __name__ == "__main__":
    web_thread = threading.Thread(target=lambda: app_web.run(host='0.0.0.0', port=10000, debug=False))
    web_thread.daemon = True
    web_thread.start()
    logger.info("🌐 Servidor web iniciado en puerto 10000")
    
    run_telegram_bot()