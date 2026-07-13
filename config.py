# config.py
# ============================================
# CONFIGURACIÓN DEL AGENTE
# ============================================

# Adzuna API (ya tienes las credenciales)
ADZUNA_APP_ID = "d71e6fac"
ADZUNA_API_KEY = "4229738c279506d405ef13c5d3c4bf4f"

# Telegram (TUS CREDENCIALES)
TELEGRAM_BOT_TOKEN = "8689965407:AAEAsajcfXecj0a3qTm-ivdFVc0yZ2B_QQg"  # <--- REEMPLAZA CON TU TOKEN
TELEGRAM_CHAT_ID = "242979528"            # <--- YA ESTÁ CONFIGURADO

# Archivos
MEMORY_FILE = "memory.json"
APPROVED_FILE = "approved_offers.json"
LOG_FILE = "agente.log"

# Configuración de búsqueda
CITIES = ["Granada", "Málaga"]
KEYWORDS = [
    "FCT", "prácticas", "prácticas FP", "DAW", "DAM", "ASIR",
    "desarrollo", "programación", "becario", "beca",
    "estudiante", "junior", "trainee"
]
MIN_SCORE = 20           # Puntuación mínima para mostrar ofertas
AUTO_APPROVE_SCORE = 70  # Puntuación para aprobar automáticamente
MAX_ITERATIONS = 3       # Número máximo de iteraciones del loop