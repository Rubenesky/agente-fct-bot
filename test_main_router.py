"""test_main_router.py - Confirma que el grafo no repite la búsqueda dentro
de la misma ejecución.

search_node consulta fuentes deterministas (Adzuna, Tecnoempleo RSS,
Teamtailor): mismas keywords, mismas fuentes, mismo momento. Repetir la
búsqueda dentro de la misma ejecución del grafo nunca encuentra ofertas
nuevas (ni classify_node ni ningún otro nodo marcan nada como "visto" entre
iteraciones - eso solo ocurre en classify_node vía db.save_offer, que
solo se alcanza después de que el router decide "classify"), así que el
router ya no debe reintentar: search_node se invoca como máximo una vez
por ejecución, sea cual sea el número de ofertas encontradas.

No se llama a ninguna API real: se mockean las funciones search_* de las
fuentes de datos, classify_offer, y las funciones de db.py/Telegram
usadas por classify_node.
"""
from unittest.mock import MagicMock, patch

import main


def make_state():
    return {"offers": [], "seen_companies": set(), "finished": False}


class TestRouterDoesNotRetrySearch:
    def test_search_node_is_called_at_most_once_per_run(self):
        """Con pocas ofertas nuevas (<5), el grafo no debe volver a
        search_node: debe pasar directamente a classify_node en vez de
        reintentar la búsqueda hasta MAX_ITERATIONS."""
        search_node_spy = MagicMock(wraps=main.search_node)

        with patch.object(main, "search_node", search_node_spy), \
             patch.object(main, "search_adzuna", return_value=[]), \
             patch.object(main, "search_tecnoempleo_rss", return_value=[]), \
             patch.object(main, "search_teamtailor", return_value=[]), \
             patch.object(main.db, "is_seen", return_value=False), \
             patch.object(main, "classify_offer", return_value=("descarte", "no encaja")), \
             patch.object(main.db, "get_error_offers", return_value=[]), \
             patch.object(main.db, "save_offer", side_effect=lambda o: 1), \
             patch.object(main, "send_offers_for_review"), \
             patch.object(main.time, "sleep"):
            app = main.create_graph()
            result = app.invoke(make_state())

        assert search_node_spy.call_count == 1
        assert result["finished"] is True

    def test_search_node_is_called_at_most_once_even_with_some_offers(self):
        """Igual que el anterior, pero con algunas ofertas encontradas
        (menos de 5) - antes esto también disparaba un reintento porque
        len(offers) < 5, aunque las fuentes ya no fueran a devolver nada
        distinto."""
        search_node_spy = MagicMock(wraps=main.search_node)
        offer = main.JobOffer(
            title="Prácticas DAW",
            location="Granada",
            mode="Presencial",
            company="Empresa X",
            url="https://ejemplo.test/oferta-1",
            source="Adzuna",
        )

        with patch.object(main, "search_node", search_node_spy), \
             patch.object(main, "search_adzuna", return_value=[offer]), \
             patch.object(main, "search_tecnoempleo_rss", return_value=[]), \
             patch.object(main, "search_teamtailor", return_value=[]), \
             patch.object(main.db, "is_seen", return_value=False), \
             patch.object(main, "classify_offer", return_value=("descarte", "no encaja")), \
             patch.object(main.db, "get_error_offers", return_value=[]), \
             patch.object(main.db, "save_offer", side_effect=lambda o: 1), \
             patch.object(main, "send_offers_for_review"), \
             patch.object(main.time, "sleep"):
            app = main.create_graph()
            app.invoke(make_state())

        assert search_node_spy.call_count == 1
