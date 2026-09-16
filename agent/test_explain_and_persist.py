"""
Tests de explain_and_persist -- la función compartida entre el
management command y el endpoint DRF (GET /api/scores/<symbol>/explain/).
Mockea explain_score (ya probado a fondo en test_services.py) para
enfocarse en la lógica propia de esta función: buscar el Score más
reciente y persistir el AgentExplanation.
"""

import datetime
from unittest.mock import patch

from django.test import TestCase

from market_data.models import Ticker
from scoring.models import Score

from .models import AgentExplanation
from .services import AgentError, explain_and_persist


class ExplainAndPersistTests(TestCase):
    def setUp(self):
        self.ticker = Ticker.objects.create(symbol="AAPL", name="Apple Inc.")

    def test_persists_agent_explanation_linked_to_latest_score(self):
        Score.objects.create(
            ticker=self.ticker, date=datetime.date(2026, 1, 1), score=50, components={}
        )
        latest = Score.objects.create(
            ticker=self.ticker, date=datetime.date(2026, 1, 2), score=70, components={}
        )
        fake_result = {
            "texto": "AAPL tiene tendencia alcista.",
            "tool_calls": [{"tool": "get_technical_indicators", "args": {"symbol": "AAPL"}}],
        }

        with patch("agent.services.explain_score", return_value=fake_result):
            explanation, error = explain_and_persist("aapl")

        self.assertIsNone(error)
        self.assertEqual(explanation.score, latest)
        self.assertEqual(explanation.texto, fake_result["texto"])
        self.assertEqual(explanation.tool_calls, fake_result["tool_calls"])
        self.assertEqual(AgentExplanation.objects.count(), 1)

    def test_returns_error_without_persisting_when_no_score_exists(self):
        with patch(
            "agent.services.explain_score",
            return_value={"texto": "sin score", "tool_calls": []},
        ):
            explanation, error = explain_and_persist("AAPL")

        self.assertIsNone(explanation)
        self.assertIn("AAPL", error)
        self.assertEqual(AgentExplanation.objects.count(), 0)

    def test_agent_error_propagates_without_being_caught(self):
        with patch(
            "agent.services.explain_score",
            side_effect=AgentError("GEMINI_API_KEY no está configurada"),
        ):
            with self.assertRaises(AgentError):
                explain_and_persist("AAPL")

        self.assertEqual(AgentExplanation.objects.count(), 0)
