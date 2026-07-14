# controller.py
import subprocess
import threading
import time
from flask import Flask, request
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

TOKEN = "8689965407:AAEAsajcfXecj0a3qTm-ivdFVc0yZ2B_QQg"

# --- Servidor web mínimo para Render ---
app_web = Flask(__name__)

@app_web.route('/')
def index():
    return "Bot de Telegram activo! 🚀"

@app_web.route('/health')
def health():
    return "OK", 200

def run_web_server():
    app_web.run(host='0.0.0.0', port=10000)

# --- Bot de Telegram ---
async def run_agent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_text("✅ Comando recibido. Ejecutando agente...")
    
    def execute():
        try:
            result = subprocess.run(
                ["python", "main.py", "once"],
                capture_output=True,
                text=True,
                timeout=300
            )
            
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
            
        except subprocess.TimeoutExpired:
            context.bot.send_message(chat_id=chat_id, text="⏰ El agente tardó demasiado (>5 minutos)")
        except Exception as e:
            context.bot.send_message(chat_id=chat_id, text=f"❌ Error: {e}")
    
    thread = threading.Thread(target=execute)
    thread.start()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 <b>Bot de control del Agente FCT</b>\n\n"
        "Comandos disponibles:\n"
        "/run_agent - Ejecutar la búsqueda de ofertas\n"
        "/status - Ver estado del agente",
        parse_mode="HTML"
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📊 <b>Estado del agente</b>\n\n"
        "✅ Bot activo en la nube (Render)\n"
        "📁 Archivos: memory.json, approved_offers.json, agente.log\n"
        "📱 Envía /run_agent para ejecutar manualmente\n"
        "⏰ El agente busca ofertas de FCT/prácticas en Granada y Málaga",
        parse_mode="HTML"
    )

def run_telegram_bot():
    """Ejecuta el bot de Telegram en un bucle independiente."""
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("run_agent", run_agent))
    app.add_handler(CommandHandler("status", status))
    
    print("🚀 Bot de control iniciado en Render.com...")
    print("📱 Comandos disponibles: /start, /run_agent, /status")
    app.run_polling()

# --- Ejecutar ambos servicios en paralelo ---
if __name__ == "__main__":
    # Ejecutar el bot de Telegram en un hilo separado
    bot_thread = threading.Thread(target=run_telegram_bot)
    bot_thread.daemon = True
    bot_thread.start()
    
    # Ejecutar el servidor web en el hilo principal
    run_web_server()