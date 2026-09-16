"""
Tests del management command backtest_score. Mockea
backtesting.management.commands.backtest_score.run_backtest -- su
propia lógica ya tiene test suite en test_services.py.
"""

from io import StringIO
from unittest.mock import patch

from django.core.management import CommandError, call_command
from django.test import TestCase

from market_data.models import Ticker


class BacktestScoreCommandTests(TestCase):
    def setUp(self):
        Ticker.objects.create(symbol="AAPL", name="Apple Inc.")

    def test_unknown_ticker_raises_command_error(self):
        with self.assertRaises(CommandError):
            call_command("backtest_score", "--ticker=NOEXISTE")

    def test_prints_reason_when_backtest_is_empty(self):
        with patch(
            "backtesting.management.commands.backtest_score.run_backtest",
            return_value={"ticker": "AAPL", "reason": "no hay suficiente histórico"},
        ):
            out = StringIO()
            call_command("backtest_score", "--ticker=AAPL", stdout=out)

        self.assertIn("no hay suficiente histórico", out.getvalue())

    def test_prints_bucket_table(self):
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

        with patch(
            "backtesting.management.commands.backtest_score.run_backtest",
            return_value=fake_result,
        ):
            out = StringIO()
            call_command("backtest_score", "--ticker=aapl", "--horizon=10", stdout=out)

        output = out.getvalue()
        self.assertIn("Backtest de AAPL", output)
        self.assertIn("horizon=10", output)
        self.assertIn("3 punto(s) totales", output)
        self.assertIn("0-40", output)
        self.assertIn("60-80", output)
        self.assertIn("-5.00%", output)
        self.assertIn("50.0%", output)

    def test_passes_horizon_argument_through(self):
        with patch(
            "backtesting.management.commands.backtest_score.run_backtest",
            return_value={"ticker": "AAPL", "reason": "sin datos"},
        ) as mock_run:
            call_command("backtest_score", "--ticker=AAPL", "--horizon=20", stdout=StringIO())

        mock_run.assert_called_once()
        _, kwargs = mock_run.call_args
        self.assertEqual(kwargs["horizon_days"], 20)
