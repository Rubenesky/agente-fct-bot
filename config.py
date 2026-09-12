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

# Token de GitHub (Personal Access Token con permiso de escritura sobre el
# repo) usado por controller.py/git_sync.py para comitear y pushear
# offers.db a git tras cada aprobación/descarte desde Telegram, ya que
# Render no tiene disco persistente (ver render.yaml) y el filesystem se
# resetea en cada redeploy. Opcional: si no está configurado, el bot sigue
# funcionando con normalidad, pero las decisiones no se sincronizan con git
# (se pierden en el próximo redeploy). Ver .env.example para instrucciones
# de cómo generarlo.
GIT_PUSH_TOKEN = os.environ.get("GIT_PUSH_TOKEN")

# Archivos
DB_FILE = "offers.db"
LOG_FILE = "agente.log"


# Configuración de búsqueda
CITIES = ["Granada", "Málaga"]
KEYWORDS = [
    "FCT", "prácticas", "prácticas FP", "DAW", "DAM", "ASIR",
    "desarrollo", "programación", "becario", "beca",
    "estudiante", "junior", "trainee", "informática", "sistemas"
]
MAX_ITERATIONS = 3       # Número máximo de iteraciones del loop

# Tope de ofertas que se clasifican (llamadas a Gemini) en una sola ejecución
# del cron - el resto se deja para la siguiente ejecución en vez de intentar
# clasificarlas todas de golpe (se llegó a ver una tanda de 74 ofertas nuevas
# en una sola corrida, lo que agota la cuota de Gemini). Configurable por
# variable de entorno para poder ajustarlo sin tocar código.
MAX_CLASSIFICATIONS_PER_RUN = int(os.environ.get("MAX_CLASSIFICATIONS_PER_RUN", "30"))

# Empresas Tier A ya mapeadas (Granada/Málaga)
TIER_A_COMPANIES = [
    "Cívica", "Nazaríes", "NTT Data", "Firmafy", "Atlax 360",
    "AvaiBook", "idealista", "Freepik", "Magnific", "Yerbabuena",
    "Aircury", "5 Digital Street", "Ideable", "MailerLite", "Nexion", "Oftex"
]