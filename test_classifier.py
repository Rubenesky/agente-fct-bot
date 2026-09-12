"""test_classifier.py - Pruebas del backoff de classify_offer ante 429 de
Gemini. La API real NUNCA se llama: se mockea classifier.client.models
.generate_content y classifier.time.sleep para poder comprobar los tiempos
de espera sin esperar de verdad."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import classifier
from classifier import classify_offer, MAX_BACKOFF_SECONDS
from google.genai import errors as genai_errors


def make_offer():
    return SimpleNamespace(
        title="Desarrollador/a Junior",
        company="Empresa Test",
        location="Granada",
        mode="Presencial",
        source="Adzuna",
        description="Prácticas de desarrollo web con Python y JavaScript.",
    )


def make_success_response():
    return SimpleNamespace(text='{"tier": "A", "justification": "Encaja en DAW."}')


def make_429_error(retry_delay: str | None = None) -> genai_errors.ClientError:
    """Construye un ClientError 429 tal y como lo lanza el SDK google-genai,
    con o sin el bloque google.rpc.RetryInfo que Gemini a veces incluye en
    los detalles del error de cuota agotada."""
    error_body = {
        "code": 429,
        "message": "You exceeded your current quota, please check your plan and billing details.",
        "status": "RESOURCE_EXHAUSTED",
        "details": [],
    }
    if retry_delay is not None:
        error_body["details"].append({
            "@type": "type.googleapis.com/google.rpc.RetryInfo",
            "retryDelay": retry_delay,
        })
    return genai_errors.ClientError(429, {"error": error_body}, None)


class TestRetryDelayFromApi:
    def test_uses_retry_delay_exposed_by_the_api(self):
        """Si el 429 trae retryDelay (p.ej. "2s"), se espera exactamente eso
        en vez de los 15s fijos de antes."""
        offer = make_offer()
        error = make_429_error(retry_delay="2s")
        mock_generate = MagicMock(side_effect=[error, make_success_response()])

        with patch.object(classifier.client.models, "generate_content", mock_generate), \
             patch.object(classifier.time, "sleep") as mock_sleep:
            tier, justification = classify_offer(offer)

        assert tier == "A"
        assert mock_sleep.call_count == 1
        waited = mock_sleep.call_args[0][0]
        assert waited == pytest.approx(2.0)

    def test_caps_retry_delay_at_max_backoff(self):
        """Un retryDelay absurdamente largo (p.ej. tras agotar la cuota
        diaria) se limita a MAX_BACKOFF_SECONDS para no dejar el proceso
        colgado media hora."""
        offer = make_offer()
        error = make_429_error(retry_delay="600s")
        mock_generate = MagicMock(side_effect=[error, make_success_response()])

        with patch.object(classifier.client.models, "generate_content", mock_generate), \
             patch.object(classifier.time, "sleep") as mock_sleep:
            classify_offer(offer)

        waited = mock_sleep.call_args[0][0]
        assert waited <= MAX_BACKOFF_SECONDS


class TestExponentialBackoffFallback:
    def test_falls_back_to_exponential_backoff_without_fixed_15_30_45(self):
        """Sin retryDelay en el error, el tiempo de espera debe crecer entre
        reintento y reintento (backoff exponencial), y NO debe coincidir con
        los antiguos valores fijos 15/30/45."""
        offer = make_offer()
        error = make_429_error(retry_delay=None)
        mock_generate = MagicMock(side_effect=[error, error, error, make_success_response()])

        with patch.object(classifier.client.models, "generate_content", mock_generate), \
             patch.object(classifier.time, "sleep") as mock_sleep, \
             patch.object(classifier.random, "uniform", return_value=0.0):
            tier, _ = classify_offer(offer)

        assert tier == "A"
        waits = [call.args[0] for call in mock_sleep.call_args_list]
        assert waits == sorted(waits)  # backoff creciente
        assert len(set(waits)) == len(waits)  # no repite el mismo valor
        assert waits != [15, 30, 45]
        assert all(w <= MAX_BACKOFF_SECONDS for w in waits)

    def test_backoff_includes_jitter(self):
        """El jitter hace que dos reintentos con el mismo número de intento
        no esperen siempre exactamente lo mismo."""
        offer = make_offer()
        error = make_429_error(retry_delay=None)
        mock_generate = MagicMock(side_effect=[error, make_success_response()])

        with patch.object(classifier.client.models, "generate_content", mock_generate), \
             patch.object(classifier.time, "sleep") as mock_sleep, \
             patch.object(classifier.random, "uniform", return_value=3.7):
            classify_offer(offer)

        waited = mock_sleep.call_args[0][0]
        assert waited != classifier.BASE_BACKOFF_SECONDS  # el jitter se sumó


class TestNonRetryableErrors:
    def test_non_429_error_returns_error_tier_without_sleeping(self):
        offer = make_offer()
        error = genai_errors.ClientError(
            400, {"error": {"code": 400, "message": "bad request", "status": "INVALID_ARGUMENT"}}, None
        )
        mock_generate = MagicMock(side_effect=error)

        with patch.object(classifier.client.models, "generate_content", mock_generate), \
             patch.object(classifier.time, "sleep") as mock_sleep:
            tier, justification = classify_offer(offer)

        assert tier == "error"
        mock_sleep.assert_not_called()

    def test_429_gives_up_after_max_retries_and_returns_error(self):
        offer = make_offer()
        error = make_429_error(retry_delay="1s")
        mock_generate = MagicMock(side_effect=[error, error, error, error])

        with patch.object(classifier.client.models, "generate_content", mock_generate), \
             patch.object(classifier.time, "sleep"):
            tier, justification = classify_offer(offer)

        assert tier == "error"


class TestSuccessPath:
    def test_classifies_successfully_without_retries(self):
        offer = make_offer()
        mock_generate = MagicMock(return_value=make_success_response())

        with patch.object(classifier.client.models, "generate_content", mock_generate), \
             patch.object(classifier.time, "sleep") as mock_sleep:
            tier, justification = classify_offer(offer)

        assert tier == "A"
        assert justification == "Encaja en DAW."
        mock_sleep.assert_not_called()
