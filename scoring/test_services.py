"""
Tests de compute_score. Las funciones de indicators.py ya están
verificadas contra valores calculados a mano en test_indicators.py, así
que acá se mockean (scoring.services.sma/rsi/atr/relative_volume) para
forzar cada rango de puntaje de forma exacta y determinística, en vez de
tener que construir series de precios gigantes que produzcan esos
valores de indicador por casualidad.

Las barras de PriceBar sí son reales (vía ORM) -- lo que se mockea es
solo el cálculo del indicador, no el acceso a datos.
"""

import datetime
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from market_data.models import PriceBar, Ticker

from .services import MIN_BARS_REQUIRED, compute_score


def _make_bars(ticker, count):
    for i in range(count):
        PriceBar.objects.create(
            ticker=ticker,
            date=datetime.date(2026, 1, 1) + datetime.timedelta(days=i),
            open=Decimal("100.0000"),
            high=Decimal("101.0000"),
            low=Decimal("99.0000"),
            close=Decimal("100.0000"),
            volume=1_000_000,
        )


class ComputeScoreInsufficientHistoryTests(TestCase):
    def test_returns_none_with_fewer_than_min_bars_required(self):
        ticker = Ticker.objects.create(symbol="AAPL", name="Apple Inc.")
        _make_bars(ticker, MIN_BARS_REQUIRED - 1)

        score, reason = compute_score(ticker)

        self.assertIsNone(score)
        self.assertIn("AAPL", reason)
        self.assertIn(str(MIN_BARS_REQUIRED - 1), reason)

    def test_computes_with_exactly_min_bars_required(self):
        ticker = Ticker.objects.create(symbol="AAPL", name="Apple Inc.")
        _make_bars(ticker, MIN_BARS_REQUIRED)

        with patch("scoring.services.sma", return_value=100.0), patch(
            "scoring.services.rsi", return_value=50.0
        ), patch("scoring.services.atr", return_value=1.5), patch(
            "scoring.services.relative_volume", return_value=1.0
        ):
            score, components = compute_score(ticker)

        self.assertIsNotNone(score)
        self.assertIsInstance(components, dict)


class ComputeScoreWeightingTests(TestCase):
    """
    Cubre cada rango de puntaje de las tres categorías (tendencia,
    momentum, volumen) forzando los valores de indicador vía mock.
    ATR14 se verifica por separado: nunca debe afectar el score.
    """

    def setUp(self):
        self.ticker = Ticker.objects.create(symbol="AAPL", name="Apple Inc.")
        _make_bars(self.ticker, MIN_BARS_REQUIRED)

    def _compute_with(self, *, close_gt_sma50, sma20_gt_sma50, rsi14, rel_volume, atr14=3.5):
        # closes[-1] es siempre 100.0 (todas las PriceBar de _make_bars).
        # Para forzar "close > sma50" (o no) se ajusta el propio sma50; el
        # sma20 se deriva relativo a ESE mismo sma50 para poder controlar
        # "sma20 > sma50" de forma independiente.
        sma50_value = 90.0 if close_gt_sma50 else 110.0
        sma20_value = sma50_value + 10.0 if sma20_gt_sma50 else sma50_value - 10.0

        def fake_sma(closes, window):
            return sma20_value if window == 20 else sma50_value

        with patch("scoring.services.sma", side_effect=fake_sma), patch(
            "scoring.services.rsi", return_value=rsi14
        ), patch("scoring.services.atr", return_value=atr14), patch(
            "scoring.services.relative_volume", return_value=rel_volume
        ):
            return compute_score(self.ticker)

    def test_full_bullish_scenario_scores_maximum_in_each_category(self):
        score, components = self._compute_with(
            close_gt_sma50=True, sma20_gt_sma50=True, rsi14=50.0, rel_volume=2.0
        )
        self.assertEqual(components["trend_points"], 40)
        self.assertEqual(components["momentum_points"], 15)  # zona neutral 40-60
        self.assertEqual(components["volume_points"], 30)
        self.assertEqual(score, 85)

    def test_no_trend_healthy_momentum_moderate_volume(self):
        score, components = self._compute_with(
            close_gt_sma50=False, sma20_gt_sma50=False, rsi14=65.0, rel_volume=1.2
        )
        self.assertEqual(components["trend_points"], 0)
        self.assertEqual(components["momentum_points"], 30)  # (60, 70]
        self.assertEqual(components["volume_points"], 15)
        self.assertEqual(score, 45)

    def test_partial_trend_overbought_extreme_low_volume(self):
        score, components = self._compute_with(
            close_gt_sma50=True, sma20_gt_sma50=False, rsi14=75.0, rel_volume=0.5
        )
        self.assertEqual(components["trend_points"], 20)
        self.assertEqual(components["momentum_points"], 5)  # > 70
        self.assertEqual(components["volume_points"], 0)
        self.assertEqual(score, 25)

    def test_partial_trend_oversold_extreme_strong_volume(self):
        score, components = self._compute_with(
            close_gt_sma50=False, sma20_gt_sma50=True, rsi14=25.0, rel_volume=1.5
        )
        self.assertEqual(components["trend_points"], 20)
        self.assertEqual(components["momentum_points"], 5)  # < 30
        self.assertEqual(components["volume_points"], 30)  # límite inclusivo >= 1.5
        self.assertEqual(score, 55)

    def test_rsi_healthy_zone_boundaries_are_inclusive_of_30_and_70(self):
        _, components_30 = self._compute_with(
            close_gt_sma50=True, sma20_gt_sma50=True, rsi14=30.0, rel_volume=1.0
        )
        _, components_70 = self._compute_with(
            close_gt_sma50=True, sma20_gt_sma50=True, rsi14=70.0, rel_volume=1.0
        )
        self.assertEqual(components_30["momentum_points"], 30)
        self.assertEqual(components_70["momentum_points"], 30)

    def test_atr_is_stored_but_never_affects_the_score(self):
        score_low_atr, components_low = self._compute_with(
            close_gt_sma50=True, sma20_gt_sma50=True, rsi14=50.0, rel_volume=2.0, atr14=0.1
        )
        score_high_atr, components_high = self._compute_with(
            close_gt_sma50=True, sma20_gt_sma50=True, rsi14=50.0, rel_volume=2.0, atr14=99.0
        )
        self.assertEqual(score_low_atr, score_high_atr)
        self.assertEqual(components_low["atr14"], 0.1)
        self.assertEqual(components_high["atr14"], 99.0)
