"""
Tests de run_backtest. Nunca golpea Neon con datos reales: todo PriceBar
es sintético, creado a mano en cada test, para poder calcular a mano el
resultado esperado.
"""

import datetime
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from market_data.models import PriceBar, Ticker
from scoring.services import MIN_BARS_REQUIRED, _compute_score_from_bars

from .services import run_backtest


def _make_bar(ticker, index, close, volume=1_000_000):
    """Un PriceBar sintético en el día `index` (2026-01-01 + index días)."""
    return PriceBar.objects.create(
        ticker=ticker,
        date=datetime.date(2026, 1, 1) + datetime.timedelta(days=index),
        open=Decimal(str(close)),
        high=Decimal(str(close)),
        low=Decimal(str(close)),
        close=Decimal(str(close)),
        volume=volume,
    )


class NoLookaheadTests(TestCase):
    def test_appending_a_future_bar_does_not_change_an_earlier_score(self):
        ticker = Ticker.objects.create(symbol="AAPL", name="Apple Inc.")
        # 60 barras reales (sin mock) con un patrón simple pero no
        # trivial, para que _compute_score_from_bars calcule indicadores
        # de verdad -- si hubiera look-ahead, agregar una barra futura
        # con un precio absurdo cambiaría sma20/rsi14/etc. de fechas
        # anteriores.
        for i in range(60):
            close = 100 + (i % 7) - 3  # oscila, nunca monótono ni plano
            _make_bar(ticker, i, close)

        bars_before = list(ticker.price_bars.order_by("date"))
        cutoff_bars = bars_before[:55]  # "hasta" el día 54 (índice 54)
        score_before, _ = _compute_score_from_bars(cutoff_bars)

        # Barra "futura" con precio absurdo (10x), bien después del corte.
        _make_bar(ticker, 200, close=1000)

        # El mismo corte (los primeros 55 bars, por fecha) no cambió --
        # se recalcula tomando la DB de nuevo para probar que
        # ticker.price_bars.order_by("date") en ese rango sigue devolviendo
        # exactamente lo mismo, y que el score con esos bars es idéntico.
        bars_after = list(ticker.price_bars.order_by("date"))[:55]
        score_after, _ = _compute_score_from_bars(bars_after)

        self.assertEqual(score_before, score_after)

    def test_run_backtest_scores_for_a_given_date_are_unaffected_by_future_bars(self):
        ticker = Ticker.objects.create(symbol="AAPL", name="Apple Inc.")
        for i in range(70):
            close = 100 + (i % 7) - 3
            _make_bar(ticker, i, close)

        result_before = run_backtest(ticker, horizon_days=5)
        points_before_by_date = {p["date"]: p["score"] for p in result_before["points"]}

        # Barra absurda muy al futuro -- no debería alterar ningún punto
        # ya calculado con fecha anterior a ella.
        _make_bar(ticker, 500, close=999999)

        result_after = run_backtest(ticker, horizon_days=5)
        points_after_by_date = {p["date"]: p["score"] for p in result_after["points"]}

        for date, score in points_before_by_date.items():
            self.assertEqual(
                points_after_by_date[date],
                score,
                f"el score de {date} cambió al agregar una barra futura -- look-ahead",
            )


class ForwardReturnCalculationTests(TestCase):
    def test_single_point_forward_return_matches_hand_calculation(self):
        ticker = Ticker.objects.create(symbol="AAPL", name="Apple Inc.")
        # MIN_BARS_REQUIRED barras hasta el día de corte (índice 49) +
        # 1 barra futura (horizon_days=1) = 51 barras en total.
        for i in range(MIN_BARS_REQUIRED - 1):
            _make_bar(ticker, i, close=50)
        _make_bar(ticker, MIN_BARS_REQUIRED - 1, close=200)  # día de corte (índice 49)
        _make_bar(ticker, MIN_BARS_REQUIRED, close=220)  # +1 sesión: +10%

        with patch("backtesting.services._compute_score_from_bars", return_value=(50, {})):
            result = run_backtest(ticker, horizon_days=1)

        self.assertEqual(result["total_points"], 1)
        point = result["points"][0]
        # (220 / 200 - 1) * 100 = 10.0
        self.assertAlmostEqual(point["forward_return_pct"], 10.0, places=9)


class BucketGroupingTests(TestCase):
    def test_buckets_group_and_average_correctly(self):
        ticker = Ticker.objects.create(symbol="AAPL", name="Apple Inc.")
        # 53 barras: MIN_BARS_REQUIRED (50) + 3, con horizon_days=1 ->
        # 3 puntos válidos (índices 49, 50, 51; futuros en 50, 51, 52).
        for i in range(MIN_BARS_REQUIRED - 1):
            _make_bar(ticker, i, close=1)  # relleno, valor irrelevante
        _make_bar(ticker, 49, close=100)  # día d0
        _make_bar(ticker, 50, close=120)  # futuro de d0 (+20%) / d1
        _make_bar(ticker, 51, close=108)  # futuro de d1 (-10%) / d2
        _make_bar(ticker, 52, close=102.6)  # futuro de d2 (-5%)

        # scores forzados en el orden en que se calculan (d0, d1, d2).
        with patch(
            "backtesting.services._compute_score_from_bars",
            side_effect=[(65, {}), (75, {}), (20, {})],
        ):
            result = run_backtest(ticker, horizon_days=1)

        self.assertEqual(result["total_points"], 3)

        buckets = result["buckets"]
        self.assertEqual(set(buckets.keys()), {"0-40", "60-80"})

        # 65 y 75 caen en "60-80": retornos +20% y -10% -> avg=5%, 1/2 ganadoras.
        self.assertEqual(buckets["60-80"]["n"], 2)
        self.assertAlmostEqual(buckets["60-80"]["avg_return_pct"], 5.0, places=9)
        self.assertAlmostEqual(buckets["60-80"]["win_rate_pct"], 50.0, places=9)

        # 20 cae en "0-40": retorno -5% -> avg=-5%, 0/1 ganadoras.
        self.assertEqual(buckets["0-40"]["n"], 1)
        self.assertAlmostEqual(buckets["0-40"]["avg_return_pct"], -5.0, places=9)
        self.assertAlmostEqual(buckets["0-40"]["win_rate_pct"], 0.0, places=9)

    def test_buckets_appear_in_ascending_score_order(self):
        ticker = Ticker.objects.create(symbol="AAPL", name="Apple Inc.")
        for i in range(MIN_BARS_REQUIRED - 1):
            _make_bar(ticker, i, close=1)
        _make_bar(ticker, 49, close=100)
        _make_bar(ticker, 50, close=110)

        with patch("backtesting.services._compute_score_from_bars", return_value=(90, {})):
            result = run_backtest(ticker, horizon_days=1)

        self.assertEqual(list(result["buckets"].keys()), ["80-100"])


class InsufficientHistoryTests(TestCase):
    def test_ticker_without_enough_bars_returns_empty_result_with_reason(self):
        ticker = Ticker.objects.create(symbol="AAPL", name="Apple Inc.")
        for i in range(10):  # muy por debajo de MIN_BARS_REQUIRED
            _make_bar(ticker, i, close=100)

        result = run_backtest(ticker)

        self.assertNotIn("buckets", result)
        self.assertIn("AAPL", result["reason"])
        self.assertEqual(result["ticker"], "AAPL")

    def test_ticker_with_no_bars_at_all_does_not_raise(self):
        ticker = Ticker.objects.create(symbol="ZZZ", name="Sin historia S.A.")

        result = run_backtest(ticker)

        self.assertNotIn("buckets", result)
        self.assertIn("reason", result)
