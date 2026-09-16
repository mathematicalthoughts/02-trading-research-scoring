"""
Tests de las 3 vistas del dashboard. Sigue el patrón ya establecido en
api/tests.py: lecturas simples sin mock, POST/servicios mockeados donde
corresponde (no se re-testea la lógica interna de
load_prices_for_ticker/compute_score/run_backtest, ya tienen su propio
test suite en market_data/scoring/backtesting).
"""

import datetime
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from agent.models import AgentExplanation
from market_data.models import PriceBar, Ticker, Watchlist, WatchlistItem
from market_data.services import PriceLoadError
from scoring.models import Score


def _make_bar(ticker, index, close=100, volume=1_000_000):
    return PriceBar.objects.create(
        ticker=ticker,
        date=datetime.date(2026, 1, 1) + datetime.timedelta(days=index),
        open=Decimal(str(close)),
        high=Decimal(str(close)),
        low=Decimal(str(close)),
        close=Decimal(str(close)),
        volume=volume,
    )


class DashboardViewTests(TestCase):
    def setUp(self):
        self.aapl = Ticker.objects.create(symbol="AAPL", name="Apple Inc.")
        self.msft = Ticker.objects.create(symbol="MSFT", name="Microsoft Corp.")
        self.watchlist = Watchlist.objects.create(name="Tech")
        WatchlistItem.objects.create(watchlist=self.watchlist, ticker=self.aapl)
        WatchlistItem.objects.create(watchlist=self.watchlist, ticker=self.msft)

    def test_renders_with_correct_template(self):
        response = self.client.get(reverse("dashboard:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "dashboard/dashboard.html")

    def test_context_shows_latest_score_per_ticker(self):
        Score.objects.create(
            ticker=self.aapl, date=datetime.date(2026, 1, 1), score=50, components={}
        )
        latest = Score.objects.create(
            ticker=self.aapl, date=datetime.date(2026, 1, 2), score=70, components={}
        )

        response = self.client.get(reverse("dashboard:dashboard"))

        entry = response.context["watchlists"][0]
        self.assertEqual(entry["watchlist"], self.watchlist)
        rows_by_symbol = {row["ticker"].symbol: row for row in entry["tickers"]}
        self.assertEqual(rows_by_symbol["AAPL"]["score"], latest)
        self.assertIsNone(rows_by_symbol["MSFT"]["score"])
        self.assertContains(response, "70")
        self.assertContains(response, "sin score")

    def test_refresh_post_calls_services_once_per_ticker(self):
        def fake_compute_score(ticker):
            if ticker.symbol == "AAPL":
                return 70, {}
            return None, "sin histórico suficiente"

        with patch("dashboard.views.load_prices_for_ticker") as mock_load, patch(
            "dashboard.views.compute_score", side_effect=fake_compute_score
        ) as mock_compute:
            response = self.client.post(
                reverse("dashboard:dashboard"),
                {"watchlist_id": self.watchlist.pk},
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_load.call_count, 2)
        self.assertEqual(mock_compute.call_count, 2)
        mock_load.assert_any_call(self.aapl, days=90)
        mock_load.assert_any_call(self.msft, days=90)

        messages = [str(m) for m in response.context["messages"]]
        self.assertEqual(len(messages), 1)
        self.assertIn("2 ticker(s) procesado(s)", messages[0])
        self.assertIn("1 score(s) actualizado(s)", messages[0])
        self.assertIn("1 omitido(s) por falta de histórico", messages[0])

    def test_refresh_post_with_price_error_does_not_break_the_rest(self):
        def fake_load(ticker, days):
            if ticker.symbol == "AAPL":
                raise PriceLoadError("boom")

        with patch("dashboard.views.load_prices_for_ticker", side_effect=fake_load), patch(
            "dashboard.views.compute_score", return_value=(80, {})
        ) as mock_compute:
            response = self.client.post(
                reverse("dashboard:dashboard"),
                {"watchlist_id": self.watchlist.pk},
                follow=True,
            )

        messages = [str(m) for m in response.context["messages"]]
        self.assertIn("1 con error de precio", messages[0])
        mock_compute.assert_called_once_with(self.msft)

    def test_refresh_post_unknown_watchlist_returns_404(self):
        response = self.client.post(reverse("dashboard:dashboard"), {"watchlist_id": 9999})
        self.assertEqual(response.status_code, 404)


class TickerDetailViewTests(TestCase):
    def setUp(self):
        self.ticker = Ticker.objects.create(symbol="AAPL", name="Apple Inc.")

    def test_unknown_ticker_returns_404(self):
        response = self.client.get(
            reverse("dashboard:ticker-detail", args=["NOEXISTE"])
        )
        self.assertEqual(response.status_code, 404)

    def test_ticker_without_score_shows_empty_state_without_crashing(self):
        response = self.client.get(
            reverse("dashboard:ticker-detail", args=[self.ticker.symbol])
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "dashboard/ticker_detail.html")
        self.assertIsNone(response.context["latest_score"])
        self.assertContains(response, "Todavía no hay un score calculado")
        self.assertNotContains(response, 'id="explainButton"')

    def test_ticker_with_score_shows_components_and_explain_button(self):
        score = Score.objects.create(
            ticker=self.ticker,
            date=datetime.date(2026, 1, 1),
            score=70,
            components={
                "sma20": 101.2,
                "sma50": 100.5,
                "rsi14": 55.3,
                "relative_volume": 1.2,
                "atr14": 2.1,
                "trend_points": 40,
                "momentum_points": 15,
                "volume_points": 15,
            },
        )

        response = self.client.get(
            reverse("dashboard:ticker-detail", args=[self.ticker.symbol])
        )

        self.assertEqual(response.context["latest_score"], score)
        self.assertEqual(response.context["score_band_class"], "band-high")
        self.assertContains(response, "70 / 100")
        self.assertContains(response, 'id="explainButton"')
        self.assertIsNone(response.context["explanation"])

    def test_existing_explanation_is_shown_without_the_button(self):
        score = Score.objects.create(
            ticker=self.ticker, date=datetime.date(2026, 1, 1), score=70, components={}
        )
        explanation = AgentExplanation.objects.create(
            score=score,
            texto="AAPL tiene tendencia alcista.",
            tool_calls=[{"tool": "get_technical_indicators", "args": {"symbol": "AAPL"}}],
        )

        response = self.client.get(
            reverse("dashboard:ticker-detail", args=[self.ticker.symbol])
        )

        self.assertEqual(response.context["explanation"], explanation)
        self.assertContains(response, "AAPL tiene tendencia alcista.")
        self.assertNotContains(response, 'id="explainButton"')

    def test_chart_data_present_when_price_bars_exist(self):
        for i in range(60):
            _make_bar(self.ticker, i, close=100 + (i % 5))

        response = self.client.get(
            reverse("dashboard:ticker-detail", args=[self.ticker.symbol])
        )

        self.assertIsNotNone(response.context["chart_data"])
        self.assertEqual(len(response.context["chart_data"]["labels"]), 60)
        self.assertContains(response, "priceChartData")

    def test_chart_data_is_none_without_price_bars(self):
        response = self.client.get(
            reverse("dashboard:ticker-detail", args=[self.ticker.symbol])
        )

        self.assertIsNone(response.context["chart_data"])
        self.assertNotContains(response, "priceChartData")


class BacktestViewTests(TestCase):
    def setUp(self):
        self.ticker = Ticker.objects.create(symbol="AAPL", name="Apple Inc.")

    def test_unknown_ticker_returns_404(self):
        response = self.client.get(
            reverse("dashboard:ticker-backtest", args=["NOEXISTE"])
        )
        self.assertEqual(response.status_code, 404)

    def test_insufficient_history_shows_reason_and_always_shows_limitations(self):
        for i in range(5):
            _make_bar(self.ticker, i)

        response = self.client.get(
            reverse("dashboard:ticker-backtest", args=[self.ticker.symbol])
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "dashboard/backtest.html")
        self.assertIn("reason", response.context["result"])
        self.assertContains(response, "No se pudo correr el backtest")
        # las limitaciones se muestran SIEMPRE, con o sin datos.
        self.assertContains(response, "Limitaciones de este backtest")

    def test_valid_backtest_renders_bucket_table_and_chart_data(self):
        fake_result = {
            "ticker": "AAPL",
            "horizon_days": 10,
            "total_points": 3,
            "points": [],
            "buckets": {
                "0-40": {"n": 1, "avg_return_pct": -5.0, "win_rate_pct": 0.0},
                "60-80": {"n": 2, "avg_return_pct": 5.0, "win_rate_pct": 50.0},
            },
        }

        with patch("dashboard.views.run_backtest", return_value=fake_result) as mock_run:
            response = self.client.get(
                reverse("dashboard:ticker-backtest", args=[self.ticker.symbol])
            )

        mock_run.assert_called_once_with(self.ticker, horizon_days=10)
        self.assertContains(response, "0-40")
        self.assertContains(response, "60-80")
        self.assertContains(response, "-5.00%")
        self.assertContains(response, "50.0%")
        self.assertContains(response, "backtestChartData")
        self.assertEqual(
            response.context["chart_data"],
            {"labels": ["0-40", "60-80"], "avg_return_pct": [-5.0, 5.0]},
        )
        # las limitaciones también se muestran cuando SÍ hay datos.
        self.assertContains(response, "Limitaciones de este backtest")

    def test_horizon_days_query_param_is_passed_through(self):
        with patch(
            "dashboard.views.run_backtest",
            return_value={"ticker": "AAPL", "reason": "sin datos"},
        ) as mock_run:
            self.client.get(
                reverse("dashboard:ticker-backtest", args=[self.ticker.symbol]),
                {"horizon_days": "20"},
            )

        mock_run.assert_called_once_with(self.ticker, horizon_days=20)

    def test_invalid_horizon_days_falls_back_to_default(self):
        with patch(
            "dashboard.views.run_backtest",
            return_value={"ticker": "AAPL", "reason": "sin datos"},
        ) as mock_run:
            self.client.get(
                reverse("dashboard:ticker-backtest", args=[self.ticker.symbol]),
                {"horizon_days": "no-es-un-numero"},
            )

        mock_run.assert_called_once_with(self.ticker, horizon_days=10)
