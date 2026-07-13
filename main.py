"""
AGENTE DE BÚSQUEDA DE PRÁCTICAS FCT - VERSIÓN 10/10
=====================================================
Características:
- Búsqueda en 7 fuentes: Adzuna, Tecnoempleo, Indeed, LinkedIn, InfoJobs, Glassdoor, Trabajos.com
- Filtro avanzado por relevancia (puntuación 0-100)
- Memoria persistente (no repite ofertas vistas)
- Programación diaria automática
- Notificaciones por Telegram
- Logging completo
- Manejo de errores robusto
"""

import sys
import io
import json
import logging
import os
import time
from datetime import datetime
from typing import TypedDict
from pydantic import BaseModel
from langgraph.graph import StateGraph, START
import requests
import feedparser
import schedule
from bs4 import BeautifulSoup

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
# CONFIGURACIÓN
# ============================================
ADZUNA_APP_ID = "d71e6fac"
ADZUNA_API_KEY = "4229738c279506d405ef13c5d3c4bf4f"

TELEGRAM_BOT_TOKEN = "8689965407:AAEAsajcfXecj0a3qTm-ivdFVc0yZ2B_QQg"
TELEGRAM_CHAT_ID = "242979528"

MEMORY_FILE = "memory.json"
APPROVED_FILE = "approved_offers.json"
LOG_FILE = "agente.log"

# Configuración de búsqueda
CITIES = ["Granada", "Málaga"]
KEYWORDS = [
    "FCT", "prácticas", "prácticas FP", "DAW", "DAM", "ASIR",
    "desarrollo", "programación", "becario", "beca",
    "estudiante", "junior", "trainee", "informática", "sistemas"
]
MIN_SCORE = 20
AUTO_APPROVE_SCORE = 70
MAX_ITERATIONS = 3

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

class State(TypedDict):
    offers: list[JobOffer]
    seen_companies: set[str]
    iteration: int
    finished: bool

# ============================================
# MEMORIA PERSISTENTE
# ============================================
class Memory:
    def __init__(self, file=MEMORY_FILE):
        self.file = file
        self.seen_urls = self.load()
    
    def load(self):
        try:
            with open(self.file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return []
    
    def save(self):
        with open(self.file, 'w', encoding='utf-8') as f:
            json.dump(self.seen_urls, f)
    
    def add(self, url):
        if url not in self.seen_urls and url != '#':
            self.seen_urls.append(url)
            self.save()
    
    def seen(self, url):
        return url in self.seen_urls

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

def send_offers_summary(offers):
    """Envía un resumen de ofertas por Telegram."""
    if not offers:
        return
    
    message = f"🔍 <b>Nuevas ofertas encontradas</b>\n📅 {datetime.now().strftime('%d/%m/%Y %H:%M')}\n"
    message += f"📊 Total: {len(offers)}\n\n"
    
    for i, offer in enumerate(offers[:10], 1):
        message += f"{i}. <b>{offer.title[:80]}</b>\n"
        message += f"   🏢 {offer.company}\n"
        message += f"   📍 {offer.location}\n"
        message += f"   📌 {offer.source}\n"
        message += f"   🔗 <a href='{offer.url}'>Ver oferta</a>\n\n"
    
    send_telegram(message)

# ============================================
# FILTRO DE RELEVANCIA
# ============================================
def get_relevance_score(offer: JobOffer) -> int:
    """Asigna puntuación de relevancia (0-100)."""
    text = (offer.title + " " + offer.description).lower()
    
    # Palabras obligatorias
    required = ["daw", "dam", "asir", "desarrollo", "programación", "programador",
                "java", "python", "javascript", "sql", "backend", "frontend",
                "software", "informática", "sistemas", "web", "tecnología"]
    has_required = any(k in text for k in required)
    if not has_required:
        return 0
    
    # Palabras excluidas
    excluded = ["limpiador", "limpieza", "veterinario", "veterinaria", "fisioterapeuta",
                "enfermero", "camarero", "cocina", "recepcionista", "conductor", "repartidor",
                "comercial", "ventas", "marketing", "administrativo", "recursos humanos",
                "bodega", "operario", "almacén", "logística", "mecánico"]
    has_excluded = any(k in text for k in excluded)
    if has_excluded:
        return 0
    
    score = 0
    
    # PRIORIDAD MÁXIMA: FCT, prácticas, becario
    if "fct" in text:
        score += 60
    if "prácticas" in text or "becario" in text or "beca" in text:
        score += 50
    if "junior" in text or "trainee" in text or "estudiante" in text:
        score += 40
    
    # Palabras técnicas
    tech_high = ["daw", "dam", "asir", "desarrollo", "programación", "programador", "java", "python"]
    for k in tech_high:
        if k in text:
            score += 10
    
    return max(0, min(100, score))

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

# 3. Indeed RSS
def search_indeed_rss(keyword: str, location: str) -> list[JobOffer]:
    """Busca ofertas en Indeed RSS."""
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

# 4. LinkedIn (scraping)
def search_linkedin(keyword: str, location: str) -> list[JobOffer]:
    """Busca ofertas en LinkedIn (scraping básico)."""
    try:
        clean_keyword = keyword.replace(" ", "%20")
        url = f"https://www.linkedin.com/jobs/search?keywords={clean_keyword}&location={location}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            offers = []
            for item in soup.find_all('div', class_='base-search-card')[:5]:
                title_elem = item.find('h3', class_='base-search-card__title')
                company_elem = item.find('h4', class_='base-search-card__subtitle')
                location_elem = item.find('span', class_='job-search-card__location')
                link_elem = item.find('a', class_='base-card__full-link')
                
                if title_elem and link_elem:
                    title = title_elem.text.strip()
                    company = company_elem.text.strip() if company_elem else "LinkedIn"
                    location_text = location_elem.text.strip() if location_elem else location
                    link = link_elem.get('href', '#')
                    
                    offer = JobOffer(
                        title=title,
                        location=location_text,
                        mode="Presencial",
                        company=company,
                        url=link,
                        source="LinkedIn",
                        description=title,
                        found_date=datetime.now().isoformat()
                    )
                    offers.append(offer)
            return offers
        return []
    except Exception as e:
        logger.error(f"Error en LinkedIn: {e}")
        return []

# 5. InfoJobs (si hay RSS)
def search_infojobs_rss(keyword: str, location: str) -> list[JobOffer]:
    """Busca ofertas en InfoJobs (RSS)."""
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

# 6. Glassdoor (scraping)
def search_glassdoor(keyword: str, location: str) -> list[JobOffer]:
    """Busca ofertas en Glassdoor (scraping básico)."""
    try:
        clean_keyword = keyword.replace(" ", "-")
        url = f"https://www.glassdoor.es/jobs/{clean_keyword}-{location.lower()}-jobs"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            offers = []
            for item in soup.find_all('li', class_='react-job-listing')[:5]:
                title_elem = item.find('a', class_='jobTitle')
                company_elem = item.find('span', class_='employerName')
                location_elem = item.find('span', class_='location')
                
                if title_elem:
                    title = title_elem.text.strip()
                    company = company_elem.text.strip() if company_elem else "Glassdoor"
                    location_text = location_elem.text.strip() if location_elem else location
                    link = title_elem.get('href', '#')
                    if link and not link.startswith('http'):
                        link = f"https://www.glassdoor.es{link}"
                    
                    offer = JobOffer(
                        title=title,
                        location=location_text,
                        mode="Presencial",
                        company=company,
                        url=link,
                        source="Glassdoor",
                        description=title,
                        found_date=datetime.now().isoformat()
                    )
                    offers.append(offer)
            return offers
        return []
    except Exception as e:
        logger.error(f"Error en Glassdoor: {e}")
        return []

# 7. Trabajos.com (scraping)
def search_trabajos_com(keyword: str, location: str) -> list[JobOffer]:
    """Busca ofertas en Trabajos.com."""
    try:
        clean_keyword = keyword.replace(" ", "+")
        url = f"https://www.trabajos.com/ofertas/{clean_keyword}/{location.lower()}"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            offers = []
            for item in soup.find_all('div', class_='offer')[:5]:
                title_elem = item.find('h3')
                company_elem = item.find('span', class_='company')
                location_elem = item.find('span', class_='location')
                link_elem = item.find('a')
                
                if title_elem and link_elem:
                    title = title_elem.text.strip()
                    company = company_elem.text.strip() if company_elem else "Trabajos.com"
                    location_text = location_elem.text.strip() if location_elem else location
                    link = link_elem.get('href', '#')
                    if link and not link.startswith('http'):
                        link = f"https://www.trabajos.com{link}"
                    
                    offer = JobOffer(
                        title=title,
                        location=location_text,
                        mode="Presencial",
                        company=company,
                        url=link,
                        source="Trabajos.com",
                        description=title,
                        found_date=datetime.now().isoformat()
                    )
                    offers.append(offer)
            return offers
        return []
    except Exception as e:
        logger.error(f"Error en Trabajos.com: {e}")
        return []

# ============================================
# NODOS LANGGRAPH
# ============================================
def search_node(state: State) -> State:
    """Nodo de búsqueda principal con todas las fuentes."""
    all_found = []
    total_raw = 0
    total_relevant = 0
    
    memory = Memory()
    
    for city in CITIES:
        logger.info(f"📌 Buscando en {city}...")
        for keyword in KEYWORDS:
            logger.info(f"  - {keyword}")
            
            # 1. Adzuna
            adzuna_offers = search_adzuna(keyword, city)
            if adzuna_offers:
                total_raw += len(adzuna_offers)
                relevant = [o for o in adzuna_offers if get_relevance_score(o) >= MIN_SCORE]
                total_relevant += len(relevant)
                if relevant:
                    logger.info(f"    + Adzuna: {len(relevant)} relevantes (de {len(adzuna_offers)})")
                    all_found.extend(relevant)
            
            # 2. Tecnoempleo
            tecno_offers = search_tecnoempleo_rss(keyword, city)
            if tecno_offers:
                total_raw += len(tecno_offers)
                relevant = [o for o in tecno_offers if get_relevance_score(o) >= MIN_SCORE]
                total_relevant += len(relevant)
                if relevant:
                    logger.info(f"    + Tecnoempleo: {len(relevant)} relevantes (de {len(tecno_offers)})")
                    all_found.extend(relevant)
            
            # 3. Indeed
            indeed_offers = search_indeed_rss(keyword, city)
            if indeed_offers:
                total_raw += len(indeed_offers)
                relevant = [o for o in indeed_offers if get_relevance_score(o) >= MIN_SCORE]
                total_relevant += len(relevant)
                if relevant:
                    logger.info(f"    + Indeed: {len(relevant)} relevantes (de {len(indeed_offers)})")
                    all_found.extend(relevant)
            
            # 4. LinkedIn
            linkedin_offers = search_linkedin(keyword, city)
            if linkedin_offers:
                total_raw += len(linkedin_offers)
                relevant = [o for o in linkedin_offers if get_relevance_score(o) >= MIN_SCORE]
                total_relevant += len(relevant)
                if relevant:
                    logger.info(f"    + LinkedIn: {len(relevant)} relevantes (de {len(linkedin_offers)})")
                    all_found.extend(relevant)
            
            # 5. InfoJobs
            info_offers = search_infojobs_rss(keyword, city)
            if info_offers:
                total_raw += len(info_offers)
                relevant = [o for o in info_offers if get_relevance_score(o) >= MIN_SCORE]
                total_relevant += len(relevant)
                if relevant:
                    logger.info(f"    + InfoJobs: {len(relevant)} relevantes (de {len(info_offers)})")
                    all_found.extend(relevant)
            
            # 6. Glassdoor
            glassdoor_offers = search_glassdoor(keyword, city)
            if glassdoor_offers:
                total_raw += len(glassdoor_offers)
                relevant = [o for o in glassdoor_offers if get_relevance_score(o) >= MIN_SCORE]
                total_relevant += len(relevant)
                if relevant:
                    logger.info(f"    + Glassdoor: {len(relevant)} relevantes (de {len(glassdoor_offers)})")
                    all_found.extend(relevant)
            
            # 7. Trabajos.com
            trabajos_offers = search_trabajos_com(keyword, city)
            if trabajos_offers:
                total_raw += len(trabajos_offers)
                relevant = [o for o in trabajos_offers if get_relevance_score(o) >= MIN_SCORE]
                total_relevant += len(relevant)
                if relevant:
                    logger.info(f"    + Trabajos.com: {len(relevant)} relevantes (de {len(trabajos_offers)})")
                    all_found.extend(relevant)
            
            time.sleep(0.5)
    
    logger.info(f"Resumen: {total_raw} ofertas totales, {total_relevant} relevantes")
    
    # Filtrar ofertas ya vistas
    new_offers = []
    for offer in all_found:
        if not memory.seen(offer.url):
            new_offers.append(offer)
            memory.add(offer.url)
    
    # Eliminar duplicados por URL
    seen_urls = set()
    unique_offers = []
    for offer in new_offers:
        if offer.url not in seen_urls:
            seen_urls.add(offer.url)
            unique_offers.append(offer)
    
    # Ordenar por relevancia
    unique_offers.sort(key=lambda o: get_relevance_score(o), reverse=True)
    
    if not unique_offers:
        logger.info("No se encontraron ofertas nuevas.")
    else:
        logger.info(f"Total ofertas nuevas encontradas: {len(unique_offers)}")
    
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
    """Decide si repetir la búsqueda o pasar a aprobación."""
    if len(state['offers']) < 5 and state['iteration'] < MAX_ITERATIONS:
        return "search"
    else:
        return "approve"

def approve_node(state: State) -> State:
    """Nodo de aprobación manual."""
    approved_offers = []
    
    if not state['offers']:
        logger.info("No hay ofertas nuevas para aprobar.")
    else:
        logger.info(f"Mostrando {len(state['offers'])} ofertas para aprobar")
        
        auto_approve = [o for o in state['offers'] if get_relevance_score(o) >= AUTO_APPROVE_SCORE]
        manual_offers = [o for o in state['offers'] if get_relevance_score(o) < AUTO_APPROVE_SCORE]
        
        if auto_approve:
            approved_offers.extend(auto_approve)
            logger.info(f"Aprobadas automaticamente: {len(auto_approve)} (puntuacion > {AUTO_APPROVE_SCORE})")
        
        if manual_offers:
            logger.info(f"Manual: {len(manual_offers)} ofertas")
            for idx, offer in enumerate(manual_offers, 1):
                score = get_relevance_score(offer)
                print(f"\n--- Oferta {idx}/{len(manual_offers)} (Relevancia: {score}/100) ---")
                print(f"Titulo: {offer.title}")
                print(f"Empresa: {offer.company}")
                print(f"Ubicacion: {offer.location}")
                print(f"Modalidad: {offer.mode}")
                print(f"Fuente: {offer.source}")
                print(f"URL: {offer.url}")
                approval = input("Aprobar esta oferta? (s/n): ").strip().lower()
                if approval == 's' or approval == 'si':
                    approved_offers.append(offer)
    
    if approved_offers:
        with open(APPROVED_FILE, 'a', encoding='utf-8') as f:
            for offer in approved_offers:
                f.write(offer.model_dump_json(indent=2) + '\n')
        
        logger.info(f"{len(approved_offers)} ofertas aprobadas guardadas")
        send_offers_summary(approved_offers)
    else:
        logger.info("No se aprobaron ofertas")
    
    state['offers'] = approved_offers
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
    graph.add_node("approve_node", approve_node)
    
    graph.add_edge(START, "search_node")
    graph.add_edge("search_node", "filter_node")
    graph.add_edge("filter_node", "reflect_node")
    
    graph.add_conditional_edges(
        "reflect_node",
        router,
        {
            "search": "search_node",
            "approve": "approve_node"
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
    
    app = create_graph()
    
    initial_state: State = {
        "offers": [],
        "seen_companies": set(),
        "iteration": 0,
        "finished": False
    }
    
    try:
        result = app.invoke(initial_state)
        logger.info(f"Ejecucion completada: {len(result['offers'])} ofertas aprobadas")
        return result
    except Exception as e:
        logger.error(f"Error en la ejecucion: {e}")
        send_telegram(f"Error en el agente: {e}")
        return None

# ============================================
# EJECUCIÓN PRINCIPAL
# ============================================
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "once":
        run_agent()
    else:
        logger.info("Agente en modo programado (diario a las 9:00)")
        run_agent()
        schedule.every().day.at("09:00").do(run_agent)
        
        while True:
            schedule.run_pending()
            time.sleep(60)