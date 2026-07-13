# controller.py
import subprocess
import threading
import time
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

TOKEN = "8689965407:AAEAsajcfXecj0a3qTm-ivdFVc0yZ2B_QQg"

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
            msg = "✅ Agente completado!\n\n"
            if result.stdout:
                msg += f"📊 Output:\n{result.stdout[:800]}"
            if result.stderr:
                msg += f"\n\n⚠️ Errores:\n{result.stderr[:300]}"
            if len(msg) > 4000:
                msg = msg[:4000] + "... (truncado)"
            context.bot.send_message(chat_id=chat_id, text=msg)
        except subprocess.TimeoutExpired:
            context.bot.send_message(chat_id=chat_id, text="⏰ El agente tardó demasiado (>5 minutos)")
        except Exception as e:
            context.bot.send_message(chat_id=chat_id, text=f"❌ Error: {e}")
    
    thread = threading.Thread(target=execute)
    thread.start()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 Bot de control del Agente FCT\n\n"
        "Comandos disponibles:\n"
        "/run_agent - Ejecutar la búsqueda de ofertas\n"
        "/status - Ver estado del agente"
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📊 Estado del agente:\n\n"
        "✅ Bot activo en la nube\n"
        "📁 Archivos: memory.json, approved_offers.json, agente.log\n"
        "📱 Envía /run_agent para ejecutar manualmente"
    )

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("run_agent", run_agent))
    app.add_handler(CommandHandler("status", status))
    
    print("🚀 Bot de control iniciado en Render.com...")
    print("📱 Comandos disponibles: /start, /run_agent, /status")
    app.run_polling()

if __name__ == "__main__":
    main()