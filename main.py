"""
AGENTE DE BÚSQUEDA DE PRÁCTICAS FCT
=====================================================
Características:
- Búsqueda en fuentes activas: Adzuna, Tecnoempleo, webs de empresas Tier A vía
  Teamtailor (Fase 6, piloto: Freepik/Magnific, idealista/AvaiBook). Indeed e
  InfoJobs pendientes, ver notas junto a sus funciones de búsqueda
- Clasificación Tier A / Tier B / descarte vía API de Gemini (classifier.py)
- Memoria persistente (no repite ofertas vistas)
- Sin aprobación automática: todo lo no descartado se notifica por Telegram
  para revisión humana
- Notificaciones por Telegram
- Logging completo
- Manejo de errores robusto
- MUESTRA LAS OFERTAS EN LA SALIDA ESTÁNDAR
"""

import sys
import io
import logging
import re
import time
from datetime import datetime
from typing import TypedDict
from pydantic import BaseModel
from langgraph.graph import StateGraph, START
import requests
import feedparser

from config import (
    ADZUNA_APP_ID, ADZUNA_API_KEY,
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
    LOG_FILE,
    CITIES, KEYWORDS, MAX_ITERATIONS
)
from classifier import classify_offer
import db

# ============================================
# SOLUCIÓN PARA ERRORES DE ENCODING EN WINDOWS
# ============================================
if sys.platform == 'win32':
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')
    except AttributeError:
        pass

# ============================================
# LOGGING CON ENCODING UTF-8
# ============================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ============================================
# MODELOS Y ESTADO
# ============================================
class JobOffer(BaseModel):
    title: str
    location: str
    mode: str
    company: str
    url: str
    source: str = "Desconocido"
    description: str = ""
    found_date: str = ""
    tier: str = ""
    justification: str = ""
    db_id: int = 0

class State(TypedDict):
    offers: list[JobOffer]
    seen_companies: set[str]
    iteration: int
    finished: bool

# ============================================
# NOTIFICACIONES TELEGRAM
# ============================================
def send_telegram(message):
    """Envía un mensaje por Telegram."""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        response = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }, timeout=10)
        return response.status_code == 200
    except Exception as e:
        logger.error(f"Error enviando Telegram: {e}")
        return False

TIER_EMOJI = {"A": "🅰️", "B": "🅱️", "error": "⚠️"}

def format_offer_message(offer) -> str:
    emoji = TIER_EMOJI.get(offer.tier, "❔")
    message = f"{emoji} <b>Tier {offer.tier}</b> — <b>{offer.title[:80]}</b>\n"
    message += f"🏢 {offer.company}\n"
    message += f"📍 {offer.location} ({offer.mode})\n"
    message += f"📌 {offer.source}\n"
    message += f"💬 {offer.justification}\n"
    message += f"🔗 <a href='{offer.url}'>Ver oferta</a>"
    return message

def send_offer_for_review(offer):
    """Envía una oferta individual por Telegram con botones inline
    Aprobar/Descartar. callback_data usa offer.db_id (rowid en SQLite) en vez
    de la URL completa, porque callback_data tiene un límite de 64 bytes."""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        response = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": format_offer_message(offer),
            "parse_mode": "HTML",
            "reply_markup": {
                "inline_keyboard": [[
                    {"text": "✅ Aprobar", "callback_data": f"approve:{offer.db_id}"},
                    {"text": "❌ Descartar", "callback_data": f"reject:{offer.db_id}"}
                ]]
            }
        }, timeout=10)
        return response.status_code == 200
    except Exception as e:
        logger.error(f"Error enviando oferta a Telegram: {e}")
        return False

def send_offers_for_review(offers):
    """Envía un aviso con el total, seguido de un mensaje por oferta (cada
    una con sus propios botones de aprobación)."""
    if not offers:
        return

    send_telegram(
        f"🔍 <b>{len(offers)} nuevas ofertas para revisar</b>\n"
        f"📅 {datetime.now().strftime('%d/%m/%Y %H:%M')}"
    )
    for offer in offers:
        send_offer_for_review(offer)

# ============================================
# FUENTES DE DATOS
# ============================================

# 1. Adzuna API
def search_adzuna(keyword: str, location: str) -> list[JobOffer]:
    """Busca ofertas en Adzuna API."""
    url = "https://api.adzuna.com/v1/api/jobs/es/search/1"
    params = {
        "app_id": ADZUNA_APP_ID,
        "app_key": ADZUNA_API_KEY,
        "what": keyword,
        "where": location,
        "results_per_page": 10,
        "content-type": "application/json"
    }
    
    try:
        response = requests.get(url, params=params, timeout=15)
        if response.status_code == 200:
            data = response.json()
            offers = []
            for item in data.get('results', []):
                location_data = item.get('location', {})
                city = location_data.get('display_name', location) if isinstance(location_data, dict) else location
                
                description = item.get('description', '')
                mode = "Presencial"
                if "remoto" in description.lower():
                    mode = "Remoto"
                elif "híbrida" in description.lower() or "hibrida" in description.lower():
                    mode = "Híbrida"
                
                company_data = item.get('company', {})
                company = company_data.get('display_name', 'Empresa desconocida') if isinstance(company_data, dict) else 'Empresa desconocida'
                
                offer = JobOffer(
                    title=item.get('title', 'Sin título'),
                    location=city,
                    mode=mode,
                    company=company,
                    url=item.get('redirect_url', '#'),
                    source="Adzuna",
                    description=description[:500],
                    found_date=datetime.now().isoformat()
                )
                offers.append(offer)
            return offers
        else:
            return []
    except Exception as e:
        logger.error(f"Error en Adzuna: {e}")
        return []

# 2. Tecnoempleo RSS
def search_tecnoempleo_rss(keyword: str, location: str) -> list[JobOffer]:
    """Busca ofertas en Tecnoempleo RSS."""
    try:
        clean_keyword = keyword.replace(" ", "+")
        rss_url = f"https://www.tecnoempleo.com/rss/empleo/?q={clean_keyword}"
        feed = feedparser.parse(rss_url)
        offers = []
        for entry in feed.entries[:10]:
            title = entry.title
            link = entry.link
            
            extracted_location = location
            for city in ["Granada", "Málaga", "Sevilla", "Barcelona", "Madrid", "Valencia", "Bilbao"]:
                if city in title:
                    extracted_location = city
                    break
            
            mode = "Presencial"
            if "remoto" in title.lower():
                mode = "Remoto"
            elif "híbrida" in title.lower() or "hibrida" in title.lower():
                mode = "Híbrida"
            
            company = "Tecnoempleo"
            if " en " in title:
                parts = title.split(" en ")
                if len(parts) > 1:
                    company = parts[1].split(" - ")[0].strip()
            
            offer = JobOffer(
                title=title,
                location=extracted_location,
                mode=mode,
                company=company,
                url=link,
                source="Tecnoempleo",
                description=title,
                found_date=datetime.now().isoformat()
            )
            offers.append(offer)
        return offers
    except Exception as e:
        logger.error(f"Error en Tecnoempleo: {e}")
        return []

# 3. Indeed (PENDIENTE - fuera de la lista de fuentes activas)
# rss.indeed.com devuelve 404/403 en todos los dominios probados (.com, es.indeed.com):
# el feed RSS está deprecado. Se deja la función sin usar hasta encontrar una vía
# alternativa (ver plan Fase 1).
def search_indeed_rss(keyword: str, location: str) -> list[JobOffer]:
    """Busca ofertas en Indeed RSS - INACTIVA, ver nota arriba."""
    try:
        clean_keyword = keyword.replace(" ", "+")
        rss_url = f"https://rss.indeed.com/rss?q={clean_keyword}&l={location}"
        feed = feedparser.parse(rss_url)
        offers = []
        for entry in feed.entries[:5]:
            offer = JobOffer(
                title=entry.title,
                location=location,
                mode="Presencial",
                company="Indeed",
                url=entry.link,
                source="Indeed",
                description=entry.title,
                found_date=datetime.now().isoformat()
            )
            offers.append(offer)
        return offers
    except Exception as e:
        logger.error(f"Error en Indeed: {e}")
        return []

# 4. InfoJobs (PENDIENTE - fuera de la lista de fuentes activas)
# infojobs.net bloquea peticiones simples con 405 en todo el dominio (protección
# anti-bot). Existe API oficial de partners en developer.infojobs.net que requiere
# registro. Se deja la función sin usar hasta integrar esa API (ver plan Fase 1/6).
def search_infojobs_rss(keyword: str, location: str) -> list[JobOffer]:
    """Busca ofertas en InfoJobs (RSS) - INACTIVA, ver nota arriba."""
    try:
        clean_keyword = keyword.replace(" ", "+")
        rss_url = f"https://www.infojobs.net/rss/offers?q={clean_keyword}&l={location}"
        feed = feedparser.parse(rss_url)
        offers = []
        for entry in feed.entries[:5]:
            offer = JobOffer(
                title=entry.title,
                location=location,
                mode="Presencial",
                company="InfoJobs",
                url=entry.link,
                source="InfoJobs",
                description=entry.title,
                found_date=datetime.now().isoformat()
            )
            offers.append(offer)
        return offers
    except Exception as e:
        logger.error(f"Error en InfoJobs: {e}")
        return []

# 5. Webs de empresas Tier A (Fase 6 - piloto)
# Empresas que usan Teamtailor como ATS exponen un feed JSON público en
# https://<subdominio>/jobs.json (formato JSON Feed + extensión _jobposting
# con schema.org JobPosting, incluyendo jobLocation estructurado). No hace
# falta autenticación ni scraping de HTML. Devuelve TODAS las ofertas
# abiertas de la empresa (no admite filtrar por keyword), así que se llama
# una sola vez por ejecución, no dentro del bucle de ciudades/keywords.
TIER_A_TEAMTAILOR_SOURCES = [
    ("jobs.magnific.com", "Freepik/Magnific"),
    ("idealista.teamtailor.com", "idealista/AvaiBook"),
]

def search_teamtailor(subdomain: str, company_label: str) -> list[JobOffer]:
    """Busca todas las ofertas abiertas de una empresa Tier A que usa
    Teamtailor, vía su feed JSON público."""
    try:
        url = f"https://{subdomain}/jobs.json"
        response = requests.get(url, timeout=10)
        if response.status_code != 200:
            return []
        data = response.json()
        offers = []
        for item in data.get('items', []):
            posting = item.get('_jobposting', {})
            job_locations = posting.get('jobLocation', [])
            if job_locations:
                address = job_locations[0].get('address', {})
                location = address.get('addressLocality') or 'No especificada'
            else:
                location = 'No especificada'

            mode = "Remoto" if posting.get('jobLocationType') == 'TELECOMMUTE' else "No especificado"

            description = re.sub('<[^<]+?>', ' ', item.get('content_html', ''))
            offer = JobOffer(
                title=item.get('title', 'Sin título'),
                location=location,
                mode=mode,
                company=company_label,
                url=item.get('url', '#'),
                source=f"Teamtailor ({company_label})",
                description=description[:1500],
                found_date=datetime.now().isoformat()
            )
            offers.append(offer)
        return offers
    except Exception as e:
        logger.error(f"Error en Teamtailor ({company_label}): {e}")
        return []

# ============================================
# NODOS LANGGRAPH
# ============================================
def search_node(state: State) -> State:
    """Nodo de búsqueda principal con las fuentes activas."""
    all_found = []

    for city in CITIES:
        logger.info(f"📌 Buscando en {city}...")
        for keyword in KEYWORDS:
            logger.info(f"  - {keyword}")

            # 1. Adzuna
            adzuna_offers = search_adzuna(keyword, city)
            if adzuna_offers:
                logger.info(f"    + Adzuna: {len(adzuna_offers)} ofertas")
                all_found.extend(adzuna_offers)

            # 2. Tecnoempleo
            tecno_offers = search_tecnoempleo_rss(keyword, city)
            if tecno_offers:
                logger.info(f"    + Tecnoempleo: {len(tecno_offers)} ofertas")
                all_found.extend(tecno_offers)

            # Indeed e InfoJobs: INACTIVAS (ver notas junto a search_indeed_rss /
            # search_infojobs_rss más arriba). LinkedIn, Glassdoor y Trabajos.com
            # se eliminaron del alcance v1 (fuera de las fuentes acordadas).

            time.sleep(0.5)

    # 3. Webs de empresas Tier A (Teamtailor) - fuera del bucle de ciudades/
    # keywords: el feed devuelve todas las ofertas abiertas de golpe, no
    # admite búsqueda por keyword.
    for subdomain, company_label in TIER_A_TEAMTAILOR_SOURCES:
        tt_offers = search_teamtailor(subdomain, company_label)
        if tt_offers:
            logger.info(f"    + Teamtailor ({company_label}): {len(tt_offers)} ofertas")
            all_found.extend(tt_offers)

    logger.info(f"Resumen: {len(all_found)} ofertas totales encontradas")

    # Filtrar ofertas ya vistas (persistidas en SQLite - db.save_offer las
    # marca como vistas en classify_node, una vez clasificadas)
    new_offers = [o for o in all_found if not db.is_seen(o.url)]

    # Eliminar duplicados por URL
    seen_urls = set()
    unique_offers = []
    for offer in new_offers:
        if offer.url not in seen_urls:
            seen_urls.add(offer.url)
            unique_offers.append(offer)

    if not unique_offers:
        logger.info("No se encontraron ofertas nuevas.")
    else:
        logger.info(f"Total ofertas nuevas encontradas: {len(unique_offers)}")
        
        # ============================================
        # NUEVO: MOSTRAR OFERTAS EN LA SALIDA ESTÁNDAR
        # ============================================
        print("\n--- OFERTAS NUEVAS ENCONTRADAS ---")
        for i, offer in enumerate(unique_offers[:10], 1):
            print(f"{i}. {offer.title[:80]}")
            print(f"   Empresa: {offer.company}")
            print(f"   Ubicación: {offer.location}")
            print(f"   Modalidad: {offer.mode}")
            print(f"   Fuente: {offer.source}")
            print(f"   🔗 {offer.url}")
            print("")
    
    state['offers'].extend(unique_offers)
    state['iteration'] += 1
    return state

def filter_node(state: State) -> State:
    """Filtra ofertas duplicadas por empresa."""
    filtered_offers = []
    for offer in state['offers']:
        if offer.company not in state['seen_companies']:
            filtered_offers.append(offer)
            state['seen_companies'].add(offer.company)
    state['offers'] = filtered_offers
    return state

def reflect_node(state: State) -> State:
    """Nodo de reflexión."""
    return state

def router(state: State) -> str:
    """Decide si repetir la búsqueda o pasar a clasificación."""
    if len(state['offers']) < 5 and state['iteration'] < MAX_ITERATIONS:
        return "search"
    else:
        return "classify"

def classify_node(state: State) -> State:
    """
    Clasifica cada oferta con el clasificador Tier A/B/descarte (API de Gemini)
    y la guarda en SQLite (db.save_offer) - esto es lo que la marca como "vista"
    para futuras ejecuciones. No hay aprobación automática: todo lo que no sea
    "descarte" según los criterios definidos se notifica por Telegram y queda
    con status 'pending' hasta que el usuario decida (Fase 4). Un fallo del
    clasificador nunca se trata como descarte (ver classify_offer).
    """
    if not state['offers']:
        logger.info("No hay ofertas nuevas para clasificar.")
        state['offers'] = []
        state['finished'] = True
        return state

    logger.info(f"Clasificando {len(state['offers'])} ofertas...")

    to_review = []
    for offer in state['offers']:
        tier, justification = classify_offer(offer)
        offer.tier = tier
        offer.justification = justification
        offer.db_id = db.save_offer(offer)

        if tier == "descarte":
            logger.info(f"❌ Descartada: {offer.title[:50]} — {justification}")
        else:
            logger.info(f"✅ Tier {tier}: {offer.title[:50]} — {justification}")
            to_review.append(offer)

    if to_review:
        send_offers_for_review(to_review)
    else:
        logger.info("Ninguna oferta pasó la clasificación")

    state['offers'] = to_review
    state['finished'] = True
    return state

# ============================================
# CONSTRUCCIÓN DEL GRAFO
# ============================================
def create_graph():
    """Crea y devuelve el grafo LangGraph."""
    graph = StateGraph(State)
    
    graph.add_node("search_node", search_node)
    graph.add_node("filter_node", filter_node)
    graph.add_node("reflect_node", reflect_node)
    graph.add_node("classify_node", classify_node)

    graph.add_edge(START, "search_node")
    graph.add_edge("search_node", "filter_node")
    graph.add_edge("filter_node", "reflect_node")

    graph.add_conditional_edges(
        "reflect_node",
        router,
        {
            "search": "search_node",
            "classify": "classify_node"
        }
    )
    
    return graph.compile()

# ============================================
# FUNCIÓN PRINCIPAL
# ============================================
def run_agent():
    """Ejecuta el agente una vez."""
    logger.info("=" * 50)
    logger.info("Iniciando agente de busqueda de practicas FCT")
    logger.info(datetime.now().strftime('%d/%m/%Y %H:%M:%S'))
    logger.info("=" * 50)

    db.init_db()

    app = create_graph()
    
    initial_state: State = {
        "offers": [],
        "seen_companies": set(),
        "iteration": 0,
        "finished": False
    }
    
    try:
        result = app.invoke(initial_state)
        logger.info(f"Ejecucion completada: {len(result['offers'])} ofertas notificadas para revisión")
        return result
    except Exception as e:
        logger.error(f"Error en la ejecucion: {e}")
        send_telegram(f"❌ Error en el agente: {e}")
        return None

# ============================================
# EJECUCIÓN PRINCIPAL
# ============================================
# El scheduler vive en GitHub Actions (.github/workflows/search.yml), no aquí.
# main.py siempre se ejecuta una sola vez y termina.
if __name__ == "__main__":
    run_agent()