"""
Tests del management command explain_score. Mockea
agent.management.commands.explain_score.explain_and_persist (el límite
entre el comando y el agente) -- la lógica de explain_and_persist en sí
ya está cubierta en test_services.py.
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

    def test_prints_texto_and_tool_calls_when_persisted(self):
        score = Score.objects.create(
            ticker=self.ticker, date=datetime.date(2026, 1, 1), score=70, components={}
        )
        explanation = AgentExplanation.objects.create(
            score=score,
            texto="AAPL tiene tendencia alcista.",
            tool_calls=[{"tool": "get_technical_indicators", "args": {"symbol": "AAPL"}}],
        )

        with patch(
            "agent.management.commands.explain_score.explain_and_persist",
            return_value=(explanation, None),
        ):
            out = StringIO()
            call_command("explain_score", "--symbol=AAPL", stdout=out)

        self.assertIn("AAPL tiene tendencia alcista.", out.getvalue())
        self.assertIn("get_technical_indicators", out.getvalue())
        self.assertIn("AgentExplanation guardada.", out.getvalue())

    def test_prints_placeholder_when_no_tools_were_invoked(self):
        score = Score.objects.create(
            ticker=self.ticker, date=datetime.date(2026, 1, 1), score=70, components={}
        )
        explanation = AgentExplanation.objects.create(
            score=score, texto="texto sin tools.", tool_calls=[]
        )

        with patch(
            "agent.management.commands.explain_score.explain_and_persist",
            return_value=(explanation, None),
        ):
            out = StringIO()
            call_command("explain_score", "--symbol=AAPL", stdout=out)

        self.assertIn("(ninguna)", out.getvalue())

    def test_reports_error_when_no_score_exists(self):
        with patch(
            "agent.management.commands.explain_score.explain_and_persist",
            return_value=(None, "No hay Score persistido para 'AAPL' -- ..."),
        ):
            err = StringIO()
            call_command("explain_score", "--symbol=AAPL", stdout=StringIO(), stderr=err)

        self.assertIn("No hay Score persistido", err.getvalue())

    def test_agent_error_is_reported_without_crashing(self):
        with patch(
            "agent.management.commands.explain_score.explain_and_persist",
            side_effect=AgentError("GEMINI_API_KEY no está configurada"),
        ):
            err = StringIO()
            call_command("explain_score", "--symbol=AAPL", stdout=StringIO(), stderr=err)

        self.assertEqual(AgentExplanation.objects.count(), 0)
        self.assertIn("GEMINI_API_KEY", err.getvalue())
