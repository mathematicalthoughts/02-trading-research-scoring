"""
Tests de GET /api/scores/<symbol>/ y GET /api/scores/<symbol>/explain/.
explain/ mockea explain_and_persist -- su propia lógica ya tiene test
suite en agent/test_explain_and_persist.py; acá solo se prueba que la
vista arma la response (200/404/502) correcta.
"""

import datetime
from unittest.mock import patch

from django.core.cache import cache
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from agent.models import AgentExplanation
from agent.services import AgentError
from market_data.models import Ticker
from scoring.models import Score


class ScoreDetailViewTests(APITestCase):
    def test_unknown_ticker_returns_404_with_specific_message(self):
        response = self.client.get("/api/scores/NOEXISTE/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertIn("no encontrado", response.data["detail"])

    def test_ticker_without_score_returns_404_with_different_message(self):
        Ticker.objects.create(symbol="AAPL", name="Apple Inc.")
        response = self.client.get("/api/scores/aapl/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertIn("no tiene ningún score", response.data["detail"])

    def test_ticker_with_score_returns_latest_score(self):
        ticker = Ticker.objects.create(symbol="AAPL", name="Apple Inc.")
        Score.objects.create(
            ticker=ticker, date=datetime.date(2026, 1, 1), score=50, components={}
        )
        latest = Score.objects.create(
            ticker=ticker,
            date=datetime.date(2026, 1, 2),
            score=70,
            components={"trend_points": 40},
        )

        response = self.client.get("/api/scores/AAPL/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["score"], 70)
        self.assertEqual(response.data["date"], str(latest.date))
        self.assertEqual(response.data["components"], {"trend_points": 40})


class ScoreExplainViewTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.ticker = Ticker.objects.create(symbol="AAPL", name="Apple Inc.")
        self.score = Score.objects.create(
            ticker=self.ticker, date=datetime.date(2026, 1, 1), score=70, components={}
        )

    def test_success_returns_serialized_explanation(self):
        explanation = AgentExplanation.objects.create(
            score=self.score,
            texto="AAPL tiene tendencia alcista.",
            tool_calls=[{"tool": "get_technical_indicators", "args": {"symbol": "AAPL"}}],
        )

        with patch("api.views.explain_and_persist", return_value=(explanation, None)):
            response = self.client.get("/api/scores/AAPL/explain/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["texto"], "AAPL tiene tendencia alcista.")
        self.assertEqual(
            response.data["tool_calls"],
            [{"tool": "get_technical_indicators", "args": {"symbol": "AAPL"}}],
        )

    def test_no_score_returns_404(self):
        with patch(
            "api.views.explain_and_persist",
            return_value=(None, "No hay Score persistido para 'AAPL'"),
        ):
            response = self.client.get("/api/scores/AAPL/explain/")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertIn("No hay Score persistido", response.data["detail"])

    def test_agent_error_returns_502_not_500(self):
        with patch(
            "api.views.explain_and_persist",
            side_effect=AgentError("Error llamando a la API de Gemini: 503 UNAVAILABLE"),
        ):
            response = self.client.get("/api/scores/AAPL/explain/")

        self.assertEqual(response.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertIn("503", response.data["detail"])


@override_settings(REST_FRAMEWORK={"DEFAULT_THROTTLE_RATES": {"agent_explain": "5/min"}})
class ExplainThrottlingTests(APITestCase):
    def setUp(self):
        # El cache de throttling (django.core.cache) no se limpia solo
        # entre tests -- sin esto, requests de otros tests contra el
        # mismo scope "agent_explain" contaminarían el conteo acá.
        cache.clear()
        ticker = Ticker.objects.create(symbol="AAPL", name="Apple Inc.")
        score = Score.objects.create(
            ticker=ticker, date=datetime.date(2026, 1, 1), score=70, components={}
        )
        self.explanation = AgentExplanation.objects.create(
            score=score, texto="texto", tool_calls=[]
        )

    def test_sixth_request_in_a_row_is_throttled(self):
        with patch("api.views.explain_and_persist", return_value=(self.explanation, None)):
            responses = [self.client.get("/api/scores/AAPL/explain/") for _ in range(6)]

        for response in responses[:5]:
            self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(responses[5].status_code, status.HTTP_429_TOO_MANY_REQUESTS)
