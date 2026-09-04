# config.py
# ============================================
# CONFIGURACIÓN DEL AGENTE
# ============================================
import os
from dotenv import load_dotenv

load_dotenv()

ADZUNA_APP_ID = os.environ["ADZUNA_APP_ID"]
ADZUNA_API_KEY = os.environ["ADZUNA_API_KEY"]

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

# Archivos
DB_FILE = "offers.db"
LOG_FILE = "agente.log"

# Archivos legado (Fase 0/1, sustituidos por DB_FILE en la Fase 3 - se dejan
# solo como referencia para migrate_to_sqlite.py, no los usa main.py)
LEGACY_MEMORY_FILE = "memory.json"
LEGACY_APPROVED_FILE = "approved_offers.json"

# Configuración de búsqueda
CITIES = ["Granada", "Málaga"]
KEYWORDS = [
    "FCT", "prácticas", "prácticas FP", "DAW", "DAM", "ASIR",
    "desarrollo", "programación", "becario", "beca",
    "estudiante", "junior", "trainee", "informática", "sistemas"
]
MAX_ITERATIONS = 3       # Número máximo de iteraciones del loop

# Empresas Tier A ya mapeadas (Granada/Málaga)
TIER_A_COMPANIES = [
    "Cívica", "Nazaríes", "NTT Data", "Firmafy", "Atlax 360",
    "AvaiBook", "idealista", "Freepik", "Magnific", "Yerbabuena",
    "Aircury", "5 Digital Street", "Ideable", "MailerLite", "Nexion", "Oftex"
]