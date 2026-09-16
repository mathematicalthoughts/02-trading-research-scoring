"""
Tests del management command explain_score. Mockea
agent.management.commands.explain_score.explain_score (el límite entre
el comando y el agente) -- la lógica de explain_score en sí ya está
cubierta en test_services.py.
"""

import datetime
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from market_data.models import Ticker
from scoring.models import Score

from .models import AgentExplanation
from .services import AgentError


class ExplainScoreCommandTests(TestCase):
    def setUp(self):
        self.ticker = Ticker.objects.create(symbol="AAPL", name="Apple Inc.")

    def test_persists_agent_explanation_when_score_exists(self):
        Score.objects.create(
            ticker=self.ticker, date=datetime.date(2026, 1, 1), score=70, components={}
        )
        fake_result = {
            "texto": "AAPL tiene tendencia alcista.",
            "tool_calls": [{"tool": "get_technical_indicators", "args": {"symbol": "AAPL"}}],
        }

        with patch(
            "agent.management.commands.explain_score.explain_score",
            return_value=fake_result,
        ):
            out = StringIO()
            call_command("explain_score", "--symbol=AAPL", stdout=out)

        self.assertEqual(AgentExplanation.objects.count(), 1)
        explanation = AgentExplanation.objects.get()
        self.assertEqual(explanation.texto, fake_result["texto"])
        self.assertEqual(explanation.tool_calls, fake_result["tool_calls"])
        self.assertIn("AAPL tiene tendencia alcista.", out.getvalue())
        self.assertIn("get_technical_indicators", out.getvalue())

    def test_does_not_persist_when_no_score_exists(self):
        fake_result = {"texto": "Sin score todavía.", "tool_calls": []}

        with patch(
            "agent.management.commands.explain_score.explain_score",
            return_value=fake_result,
        ):
            err = StringIO()
            call_command("explain_score", "--symbol=AAPL", stdout=StringIO(), stderr=err)

        self.assertEqual(AgentExplanation.objects.count(), 0)
        self.assertIn("No hay Score persistido", err.getvalue())

    def test_agent_error_is_reported_without_crashing(self):
        with patch(
            "agent.management.commands.explain_score.explain_score",
            side_effect=AgentError("GEMINI_API_KEY no está configurada"),
        ):
            err = StringIO()
            call_command("explain_score", "--symbol=AAPL", stdout=StringIO(), stderr=err)

        self.assertEqual(AgentExplanation.objects.count(), 0)
        self.assertIn("GEMINI_API_KEY", err.getvalue())
