import subprocess
import asyncio
import threading
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

TOKEN = "8689965407:AAEAsajcfXecj0a3qTm-ivdFVc0yZ2B_QQg"

# --- Servidor web ---
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
    await update.message.reply_text("✅ Comando recibido. Ejecutando agente...")
    
    try:
        process = await asyncio.create_subprocess_exec(
            "python", "main.py", "once",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        # Timeout de 10 minutos (600 segundos)
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=600)
        stdout_text = stdout.decode('utf-8', errors='ignore')
        stderr_text = stderr.decode('utf-8', errors='ignore')
        
        # Construir mensaje
        msg = "🤖 <b>Agente ejecutado</b>\n"
        msg += f"📅 {__import__('time').strftime('%d/%m/%Y %H:%M:%S')}\n\n"
        
        if stdout_text:
            lines = stdout_text.split('\n')
            offers_found = False
            for line in lines:
                if "ofertas nuevas encontradas" in line.lower() or "ofertas relevantes" in line.lower():
                    offers_found = True
                    msg += f"📊 {line.strip()}\n"
            if not offers_found:
                msg += "📊 No se encontraron ofertas nuevas en esta ejecución.\n"
        else:
            msg += "📊 No se encontraron ofertas nuevas en esta ejecución.\n"
        
        if stderr_text:
            msg += f"\n⚠️ <b>Errores:</b>\n{stderr_text[:500]}"
        
        await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode="HTML")
        
    except asyncio.TimeoutError:
        await context.bot.send_message(chat_id=chat_id, text="⏰ El agente tardó demasiado (>10 minutos)")
    except Exception as e:
        await context.bot.send_message(chat_id=chat_id, text=f"❌ Error: {e}")

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
        "📱 Envía /run_agent para ejecutar manualmente",
        parse_mode="HTML"
    )

def run_telegram_bot():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("run_agent", run_agent))
    app.add_handler(CommandHandler("status", status))
    
    print("🚀 Bot de control iniciado en Render.com...")
    print("📱 Comandos disponibles: /start, /run_agent, /status")
    app.run_polling()

if __name__ == "__main__":
    # Servidor web en hilo separado
    web_thread = threading.Thread(target=lambda: app_web.run(host='0.0.0.0', port=10000))
    web_thread.daemon = True
    web_thread.start()
    
    # Bot de Telegram en hilo principal
    run_telegram_bot()