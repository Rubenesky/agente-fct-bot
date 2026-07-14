import subprocess
import threading
import time
import logging
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# ============================================
# CONFIGURACIÓN
# ============================================
TOKEN = "8689965407:AAEAsajcfXecj0a3qTm-ivdFVc0yZ2B_QQg"

# Configurar logging para ver qué pasa
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============================================
# SERVIDOR WEB (Flask)
# ============================================
app_web = Flask(__name__)

@app_web.route('/')
def index():
    return "Bot de Telegram activo! 🚀"

@app_web.route('/health')
def health():
    return "OK", 200

def run_web_server():
    logger.info("🌐 Iniciando servidor web en puerto 10000...")
    app_web.run(host='0.0.0.0', port=10000, debug=False, use_reloader=False)

# ============================================
# BOT DE TELEGRAM
# ============================================
async def run_agent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    logger.info(f"📩 Recibido /run_agent de {chat_id}")
    await update.message.reply_text("✅ Comando recibido. Ejecutando agente...")
    
    def execute():
        try:
            logger.info("🚀 Ejecutando main.py once...")
            result = subprocess.run(
                ["python", "main.py", "once"],
                capture_output=True,
                text=True,
                timeout=300
            )
            logger.info("✅ main.py finalizado")
            
            msg = "🤖 <b>Agente ejecutado</b>\n"
            msg += f"📅 {time.strftime('%d/%m/%Y %H:%M:%S')}\n\n"
            
            if result.stdout:
                lines = result.stdout.split('\n')
                offers_found = False
                for line in lines:
                    if "ofertas nuevas encontradas" in line.lower() or "ofertas relevantes" in line.lower():
                        offers_found = True
                        msg += f"📊 {line.strip()}\n"
                
                if not offers_found:
                    msg += "📊 No se encontraron ofertas nuevas en esta ejecución.\n"
                    msg += "🔄 El agente ha revisado todas las fuentes.\n"
            else:
                msg += "📊 No se encontraron ofertas nuevas en esta ejecución.\n"
            
            if result.stderr:
                msg += f"\n⚠️ <b>Errores:</b>\n{result.stderr[:500]}"
            
            context.bot.send_message(chat_id=chat_id, text=msg, parse_mode="HTML")
            logger.info("📨 Mensaje de resultado enviado")
            
        except subprocess.TimeoutExpired:
            context.bot.send_message(chat_id=chat_id, text="⏰ El agente tardó demasiado (>5 minutos)")
            logger.error("⏰ Timeout en main.py")
        except Exception as e:
            context.bot.send_message(chat_id=chat_id, text=f"❌ Error: {e}")
            logger.error(f"❌ Error en execute: {e}")
    
    thread = threading.Thread(target=execute)
    thread.start()
    logger.info("🧵 Hilo de ejecución iniciado")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"📩 Recibido /start de {update.effective_chat.id}")
    await update.message.reply_text(
        "🤖 <b>Bot de control del Agente FCT</b>\n\n"
        "Comandos disponibles:\n"
        "/run_agent - Ejecutar la búsqueda de ofertas\n"
        "/status - Ver estado del agente",
        parse_mode="HTML"
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"📩 Recibido /status de {update.effective_chat.id}")
    await update.message.reply_text(
        "📊 <b>Estado del agente</b>\n\n"
        "✅ Bot activo en la nube (Render)\n"
        "📁 Archivos: memory.json, approved_offers.json, agente.log\n"
        "📱 Envía /run_agent para ejecutar manualmente\n"
        "⏰ El agente busca ofertas de FCT/prácticas en Granada y Málaga",
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

# ============================================
# EJECUCIÓN PRINCIPAL (el bot va primero)
# ============================================
if __name__ == "__main__":
    # Ejecutar el servidor web en un hilo separado
    web_thread = threading.Thread(target=run_web_server)
    web_thread.daemon = True
    web_thread.start()
    logger.info("🔄 Servidor web lanzado en segundo plano")
    
    # El bot de Telegram se ejecuta en el hilo principal
    run_telegram_bot()