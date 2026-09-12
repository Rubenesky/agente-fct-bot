"""classifier.py - Clasificación Tier A / Tier B / descarte vía Gemini API (Google)."""
import logging
import random
import time
from typing import Literal

from google import genai
from google.genai import types
from google.genai import errors as genai_errors
from pydantic import BaseModel, Field

from config import GEMINI_API_KEY, TIER_A_COMPANIES

logger = logging.getLogger(__name__)

client = genai.Client(api_key=GEMINI_API_KEY)

MODEL = "gemini-3.5-flash"


class OfferClassification(BaseModel):
    tier: Literal["A", "B", "descarte"]
    justification: str = Field(description="Justificación breve (1-2 frases) de la clasificación.")


SYSTEM_PROMPT = f"""Eres un clasificador de ofertas de prácticas FCT (Formación en Centros de \
Trabajo) para un estudiante de CFGS de Desarrollo de Aplicaciones Web (DAW).

Clasifica cada oferta en exactamente una de estas categorías:

TIER A: la empresa está en esta lista de empresas objetivo ya mapeadas:
{", ".join(TIER_A_COMPANIES)}
Y además la ubicación cumple la regla de ubicación (ver abajo) Y el contenido de la oferta encaja \
en el currículum de DAW (desarrollo web/software).

TIER B: la empresa NO está en la lista de arriba, pero la ubicación cumple la regla Y el contenido \
encaja claramente en el currículum de DAW. Es candidata a promoción a Tier A en el futuro.

DESCARTE: aplica si ocurre cualquiera de estas condiciones:
- El contenido no encaja en el currículum de DAW (desarrollo web/software), aunque esté etiquetada \
como "prácticas" (p.ej. becas de otro perfil: administración, marketing, ventas, etc.)
- Es presencial fuera de Granada o Málaga sin ser remota
- Es un empleo junior a jornada completa disfrazado de prácticas (no es un puesto de prácticas real)

REGLA DE UBICACIÓN: la oferta es válida si es presencial o híbrida en Granada o Málaga, O si es \
remota (independientemente de dónde esté la empresa). Una oferta presencial fuera de Granada/Málaga \
que no sea remota se descarta siempre.

Devuelve el tier y una justificación breve (1-2 frases) de tu decisión."""


MAX_RETRIES = 3  # solo para 429 (rate limit) - otros errores no se reintentan
BASE_BACKOFF_SECONDS = 5  # base del backoff exponencial cuando la API no da retryDelay
MAX_BACKOFF_SECONDS = 60  # tope superior de espera, venga de retryDelay o del backoff exponencial


def _extract_retry_delay_seconds(error: genai_errors.APIError) -> float | None:
    """Busca el retryDelay recomendado que Gemini incluye en los detalles del
    error 429 de cuota agotada (bloque google.rpc.RetryInfo, p.ej.
    {"retryDelay": "19s"}) y lo devuelve en segundos.

    Devuelve None si el error no lo expone o no se puede parsear - el SDK
    google-genai (2.20.0) no expone un atributo retry_delay propio en
    APIError, solo el cuerpo crudo de la respuesta en `error.details`.
    """
    details = getattr(error, "details", None)
    if not isinstance(details, dict):
        return None
    error_body = details.get("error", details)
    if not isinstance(error_body, dict):
        return None
    for item in error_body.get("details", []) or []:
        if not isinstance(item, dict):
            continue
        if "RetryInfo" not in str(item.get("@type", "")):
            continue
        delay = item.get("retryDelay")
        if not delay:
            continue
        try:
            return float(str(delay).rstrip("s"))
        except (TypeError, ValueError):
            return None
    return None


def classify_offer(offer) -> tuple[str, str]:
    """Clasifica una oferta con la API de Gemini.

    Devuelve (tier, justificación). tier es 'A', 'B', 'descarte', o 'error' si la
    clasificación automática falló. Un 'error' NUNCA se trata como descarte: la
    oferta se manda igualmente a revisión humana, con el motivo del fallo como
    justificación.

    Reintenta específicamente ante 429 (límite de peticiones de Gemini) - el
    nivel gratuito admite solo ~10-15 peticiones/min, e ir clasificando muchas
    ofertas seguidas sin ritmo lo agota rápido. El tiempo de espera entre
    reintentos usa el retryDelay que la propia API recomienda en el error
    (bloque RetryInfo) cuando está presente, o si no, backoff exponencial con
    jitter (nunca los 15/30/45s fijos de antes), con un tope de
    MAX_BACKOFF_SECONDS. Otros errores (auth, cuota diaria agotada sin
    RetryInfo, etc.) no se reintentan, se marcan como 'error' directamente.
    """
    offer_text = (
        f"Título: {offer.title}\n"
        f"Empresa: {offer.company}\n"
        f"Ubicación: {offer.location}\n"
        f"Modalidad: {offer.mode}\n"
        f"Fuente: {offer.source}\n"
        f"Descripción: {offer.description[:1500]}"
    )

    for attempt in range(MAX_RETRIES + 1):
        try:
            response = client.models.generate_content(
                model=MODEL,
                contents=offer_text,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    response_json_schema=OfferClassification.model_json_schema(),
                ),
            )
            result = OfferClassification.model_validate_json(response.text)
            return result.tier, result.justification
        except genai_errors.APIError as e:
            if e.code == 429 and attempt < MAX_RETRIES:
                retry_delay = _extract_retry_delay_seconds(e)
                if retry_delay is not None:
                    wait = min(retry_delay, MAX_BACKOFF_SECONDS)
                    source = "retryDelay de la API"
                else:
                    # Backoff exponencial con jitter (base 5s: 5, 10, 20... +
                    # hasta BASE_BACKOFF_SECONDS de jitter aleatorio) en vez de
                    # las esperas fijas de antes, para no sincronizar
                    # reintentos con otros procesos que choquen con el mismo
                    # límite de cuota.
                    wait = min(
                        MAX_BACKOFF_SECONDS,
                        BASE_BACKOFF_SECONDS * (2 ** attempt) + random.uniform(0, BASE_BACKOFF_SECONDS),
                    )
                    source = "backoff exponencial con jitter"
                logger.warning(
                    f"Clasificador: límite de peticiones (429), "
                    f"reintento {attempt + 1}/{MAX_RETRIES} en {wait:.1f}s ({source})"
                )
                time.sleep(wait)
                continue
            logger.error(f"Clasificador: error de la API de Gemini ({e.code}): {e.message}")
            return "error", f"No se pudo clasificar: error de la API de Gemini ({e.code})"
        except Exception as e:
            logger.error(f"Clasificador: error inesperado: {e}")
            return "error", f"No se pudo clasificar: {e}"
