import datetime

from django.contrib.admin.sites import site
from django.test import TestCase

from market_data.models import Ticker
from scoring.models import Score

from .models import AgentExplanation


class AgentExplanationModelTests(TestCase):
    def setUp(self):
        ticker = Ticker.objects.create(symbol="AAPL", name="Apple Inc.")
        self.score = Score.objects.create(
            ticker=ticker, date=datetime.date(2026, 1, 1), score=70, components={}
        )

    def test_creates_agent_explanation(self):
        explanation = AgentExplanation.objects.create(
            score=self.score,
            texto="AAPL muestra tendencia alcista confirmada.",
            tool_calls=[{"tool": "get_technical_indicators", "args": {"symbol": "AAPL"}}],
        )
        self.assertIsNotNone(explanation.timestamp)
        self.assertIn("AAPL", str(explanation))

    def test_related_name_explanations_on_score(self):
        AgentExplanation.objects.create(score=self.score, texto="texto", tool_calls=[])
        self.assertEqual(self.score.explanations.count(), 1)


class AdminRegistrationTests(TestCase):
    def test_agent_explanation_is_registered_in_admin(self):
        self.assertIn(AgentExplanation, site._registry)
