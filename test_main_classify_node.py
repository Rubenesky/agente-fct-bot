"""test_main_classify_node.py - Pruebas del nodo classify_node de main.py:
- Respeta el tope MAX_CLASSIFICATIONS_PER_RUN (no clasifica todas las ofertas
  nuevas de golpe, deja el resto para la siguiente ejecución del cron).
- Recoge también las ofertas con tier='error' en la base de datos y las
  reintenta (lógica que antes vivía en reclassify_errors.py), respetando el
  mismo cupo por ejecución.

No se llama a la API real de Gemini ni a Telegram: se mockea
classify_offer (vía main.classify_offer), las funciones de db.py y
main.send_offers_for_review.
"""
from unittest.mock import MagicMock, patch

import pytest

import main
from main import JobOffer, classify_node


def make_offer(i: int) -> JobOffer:
    return JobOffer(
        title=f"Oferta nueva {i}",
        location="Granada",
        mode="Presencial",
        company=f"Empresa {i}",
        url=f"https://ejemplo.test/oferta-nueva-{i}",
        source="Adzuna",
        description="Prácticas DAW",
        found_date="2026-09-08T10:00:00",
    )


def make_error_row(i: int) -> dict:
    return {
        "rowid": 100 + i,
        "url": f"https://ejemplo.test/oferta-error-{i}",
        "title": f"Oferta con error {i}",
        "company": f"Empresa error {i}",
        "location": "Málaga",
        "mode": "Remoto",
        "source": "Tecnoempleo",
        "description": "Prácticas DAW",
        "tier": "error",
        "justification": "No se pudo clasificar: error de la API de Gemini (429)",
        "status": "pending",
        "found_date": "2026-09-01T10:00:00",
        "decided_date": None,
    }


def make_state(offers):
    return {"offers": offers, "seen_companies": set(), "iteration": 1, "finished": False}


@pytest.fixture(autouse=True)
def no_sleep_no_telegram():
    """Todas las pruebas de este módulo evitan esperas reales y llamadas de
    red a Telegram."""
    with patch.object(main.time, "sleep"), \
         patch.object(main, "send_offers_for_review") as mock_notify:
        yield mock_notify


class TestMaxClassificationsPerRun:
    def test_defers_new_offers_beyond_the_limit_to_the_next_run(self):
        """Con más ofertas nuevas que cupo, solo se clasifican hasta el cupo;
        el resto no se guarda en la base de datos (ni se marca como vista),
        así que search_node las volverá a encontrar en la siguiente
        ejecución."""
        offers = [make_offer(i) for i in range(10)]
        state = make_state(offers)

        with patch.object(main, "MAX_CLASSIFICATIONS_PER_RUN", 4), \
             patch.object(main, "classify_offer", return_value=("descarte", "no encaja")) as mock_classify, \
             patch.object(main.db, "get_error_offers", return_value=[]), \
             patch.object(main.db, "save_offer", side_effect=lambda o: id(o)) as mock_save:
            classify_node(state)

        # error_quota = max(1, 4 // 3) = 1, new_quota = 4 - 0 errores = 4
        assert mock_classify.call_count == 4
        assert mock_save.call_count == 4

    def test_processes_all_offers_when_under_the_limit(self):
        offers = [make_offer(i) for i in range(3)]
        state = make_state(offers)

        with patch.object(main, "MAX_CLASSIFICATIONS_PER_RUN", 30), \
             patch.object(main, "classify_offer", return_value=("A", "encaja")) as mock_classify, \
             patch.object(main.db, "get_error_offers", return_value=[]), \
             patch.object(main.db, "save_offer", side_effect=lambda o: id(o)):
            classify_node(state)

        assert mock_classify.call_count == 3


class TestErrorRetryIntegration:
    def test_retries_offers_with_tier_error_from_the_database(self, no_sleep_no_telegram):
        """classify_node debe pedir a la base de datos las filas con
        tier='error' y reintentarlas con classify_offer, igual que hacía
        reclassify_errors.py."""
        error_rows = [make_error_row(1), make_error_row(2)]
        state = make_state([])  # sin ofertas nuevas esta vez

        with patch.object(main, "MAX_CLASSIFICATIONS_PER_RUN", 30), \
             patch.object(main, "classify_offer", return_value=("B", "candidata a Tier A")) as mock_classify, \
             patch.object(main.db, "get_error_offers", return_value=error_rows) as mock_get_errors, \
             patch.object(main.db, "update_offer_classification") as mock_update:
            result = classify_node(state)

        assert mock_classify.call_count == 2
        # Se piden a la BD con algún límite positivo, no se hardcodea "todas".
        assert mock_get_errors.call_args.kwargs.get("limit") or mock_get_errors.call_args.args
        # Las filas reclasificadas se actualizan en la BD por rowid, no se
        # insertan como nuevas (a diferencia de las ofertas nuevas).
        assert mock_update.call_count == 2
        updated_rowids = {call.args[0] for call in mock_update.call_args_list}
        assert updated_rowids == {101, 102}
        # Las que pasan a B deben acabar notificándose.
        assert len(result["offers"]) == 2
        no_sleep_no_telegram.assert_called_once()

    def test_error_offer_still_error_is_not_notified_but_stays_pending(self, no_sleep_no_telegram):
        error_rows = [make_error_row(1)]
        state = make_state([])

        with patch.object(main, "MAX_CLASSIFICATIONS_PER_RUN", 30), \
             patch.object(main, "classify_offer", return_value=("error", "sigue fallando")), \
             patch.object(main.db, "get_error_offers", return_value=error_rows), \
             patch.object(main.db, "update_offer_classification") as mock_update:
            result = classify_node(state)

        mock_update.assert_called_once_with(101, "error", "sigue fallando")
        assert result["offers"] == []

    def test_shares_the_per_run_quota_between_new_offers_and_error_retries(self):
        """El cupo total (MAX_CLASSIFICATIONS_PER_RUN) no se duplica: la suma
        de ofertas nuevas + reintentos de error clasificados en una ejecución
        nunca debe superar el límite configurado."""
        offers = [make_offer(i) for i in range(20)]
        error_rows = [make_error_row(i) for i in range(20)]
        state = make_state(offers)

        def fake_get_error_offers(limit):
            return error_rows[:limit]

        with patch.object(main, "MAX_CLASSIFICATIONS_PER_RUN", 9), \
             patch.object(main, "classify_offer", return_value=("descarte", "x")) as mock_classify, \
             patch.object(main.db, "get_error_offers", side_effect=fake_get_error_offers), \
             patch.object(main.db, "save_offer", side_effect=lambda o: id(o)), \
             patch.object(main.db, "update_offer_classification"):
            classify_node(state)

        assert mock_classify.call_count == 9
