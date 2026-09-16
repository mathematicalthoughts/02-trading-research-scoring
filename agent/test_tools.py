"""
Tests de agent/tools.py. get_recent_news nunca le pega a la red real de
yfinance: mockea yf.Ticker.
"""

import datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import TestCase

from market_data.models import PriceBar, Ticker
from scoring.models import Score

from .tools import get_price_history, get_recent_news, get_technical_indicators


class GetPriceHistoryTests(TestCase):
    def test_unknown_ticker_returns_error(self):
        result = get_price_history("NOEXISTE")
        self.assertEqual(result, {"error": "ticker 'NOEXISTE' no encontrado"})

    def test_returns_bars_oldest_first(self):
        ticker = Ticker.objects.create(symbol="AAPL", name="Apple Inc.")
        for i in range(5):
            PriceBar.objects.create(
                ticker=ticker,
                date=datetime.date(2026, 1, 1) + datetime.timedelta(days=i),
                open=Decimal("100"),
                high=Decimal("101"),
                low=Decimal("99"),
                close=Decimal(f"{100 + i}"),
                volume=1000 + i,
            )

        result = get_price_history("aapl", days=3)

        self.assertEqual(result["symbol"], "AAPL")
        self.assertEqual(len(result["bars"]), 3)
        # Más antigua primero de las últimas 3 (días 2026-01-03, 04, 05).
        self.assertEqual(result["bars"][0]["date"], "2026-01-03")
        self.assertEqual(result["bars"][-1]["date"], "2026-01-05")
        self.assertEqual(result["bars"][-1]["close"], 104.0)

    def test_unexpected_error_is_caught_and_returned_as_error_dict(self):
        with patch("agent.tools.Ticker.objects.get", side_effect=Exception("boom db")):
            result = get_price_history("AAPL")
        self.assertEqual(result, {"error": "boom db"})


class GetTechnicalIndicatorsTests(TestCase):
    def test_unknown_ticker_returns_error(self):
        result = get_technical_indicators("NOEXISTE")
        self.assertEqual(result, {"error": "ticker 'NOEXISTE' no encontrado"})

    def test_unexpected_error_is_caught_and_returned_as_error_dict(self):
        with patch("agent.tools.Ticker.objects.get", side_effect=Exception("boom db")):
            result = get_technical_indicators("AAPL")
        self.assertEqual(result, {"error": "boom db"})

    def test_ticker_without_score_returns_explicit_error(self):
        Ticker.objects.create(symbol="AAPL", name="Apple Inc.")
        result = get_technical_indicators("AAPL")
        self.assertEqual(
            result, {"error": "sin score calculado, correr compute_scores primero"}
        )

    def test_returns_latest_score_with_components(self):
        ticker = Ticker.objects.create(symbol="AAPL", name="Apple Inc.")
        Score.objects.create(
            ticker=ticker,
            date=datetime.date(2026, 1, 1),
            score=50,
            components={"atr14": 1.0},
        )
        latest = Score.objects.create(
            ticker=ticker,
            date=datetime.date(2026, 1, 2),
            score=70,
            components={"atr14": 2.0, "trend_points": 40},
        )

        result = get_technical_indicators("AAPL")

        self.assertEqual(result["score"], 70)
        self.assertEqual(result["date"], "2026-01-02")
        self.assertEqual(result["components"], latest.components)


class GetRecentNewsTests(TestCase):
    def test_returns_top_3_headlines_with_expected_fields(self):
        raw_items = [
            {
                "content": {
                    "title": f"Titular {i}",
                    "provider": {"displayName": f"Fuente {i}"},
                    "canonicalUrl": {"url": f"https://example.com/{i}"},
                }
            }
            for i in range(5)
        ]
        mock_ticker = MagicMock()
        mock_ticker.news = raw_items

        with patch("agent.tools.yf.Ticker", return_value=mock_ticker):
            result = get_recent_news("aapl")

        self.assertEqual(result["symbol"], "AAPL")
        self.assertEqual(len(result["news"]), 3)
        self.assertEqual(result["news"][0]["title"], "Titular 0")
        self.assertEqual(result["news"][0]["publisher"], "Fuente 0")
        self.assertEqual(result["news"][0]["link"], "https://example.com/0")

    def test_yfinance_raising_returns_empty_list_not_error(self):
        with patch("agent.tools.yf.Ticker", side_effect=ConnectionError("boom")):
            result = get_recent_news("AAPL")

        self.assertEqual(result, {"symbol": "AAPL", "news": []})

    def test_yfinance_returning_none_returns_empty_list(self):
        mock_ticker = MagicMock()
        mock_ticker.news = None

        with patch("agent.tools.yf.Ticker", return_value=mock_ticker):
            result = get_recent_news("AAPL")

        self.assertEqual(result, {"symbol": "AAPL", "news": []})
