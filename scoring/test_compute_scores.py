"""
Tests del management command compute_scores. Mockea
scoring.management.commands.compute_scores.compute_score (el límite
entre el comando y el motor de scoring) -- la lógica de compute_score
en sí ya está cubierta en test_services.py.
"""

import datetime
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from market_data.models import Ticker

from .models import Score


class ComputeScoresCommandTests(TestCase):
    def setUp(self):
        self.aapl = Ticker.objects.create(symbol="AAPL", name="Apple Inc.")
        self.today = datetime.date.today()

    def test_running_twice_updates_instead_of_duplicating(self):
        with patch(
            "scoring.management.commands.compute_scores.compute_score",
            return_value=(77, {"sma20": 1.0}),
        ):
            call_command("compute_scores", "--tickers=AAPL", stdout=StringIO())
            call_command("compute_scores", "--tickers=AAPL", stdout=StringIO())

        self.assertEqual(Score.objects.filter(ticker=self.aapl).count(), 1)
        score = Score.objects.get(ticker=self.aapl, date=self.today)
        self.assertEqual(score.score, 77)

    def test_ticker_with_insufficient_history_does_not_break_others(self):
        msft = Ticker.objects.create(symbol="MSFT", name="Microsoft Corp.")

        def fake_compute_score(ticker):
            if ticker.symbol == "AAPL":
                return 80, {"sma20": 1.0}
            return None, f"'{ticker.symbol}' no tiene suficiente histórico."

        with patch(
            "scoring.management.commands.compute_scores.compute_score",
            side_effect=fake_compute_score,
        ):
            out = StringIO()
            call_command(
                "compute_scores", "--tickers=AAPL,MSFT", stdout=out
            )

        self.assertTrue(Score.objects.filter(ticker=self.aapl, date=self.today).exists())
        self.assertFalse(Score.objects.filter(ticker=msft).exists())
        self.assertIn("SKIP MSFT", out.getvalue())
        self.assertIn("2 ticker(s) procesado(s)", out.getvalue())
        self.assertIn("1 omitido(s) por falta de histórico", out.getvalue())
        self.assertIn("1 score(s) creado(s)/actualizado(s)", out.getvalue())

    def test_ticker_raising_unexpected_error_does_not_break_others(self):
        msft = Ticker.objects.create(symbol="MSFT", name="Microsoft Corp.")

        def fake_compute_score(ticker):
            if ticker.symbol == "MSFT":
                raise ConnectionError("boom")
            return 90, {"sma20": 1.0}

        with patch(
            "scoring.management.commands.compute_scores.compute_score",
            side_effect=fake_compute_score,
        ):
            out = StringIO()
            err = StringIO()
            call_command(
                "compute_scores", "--tickers=AAPL,MSFT", stdout=out, stderr=err
            )

        self.assertTrue(Score.objects.filter(ticker=self.aapl).exists())
        self.assertFalse(Score.objects.filter(ticker=msft).exists())
        self.assertIn("FAIL MSFT", err.getvalue())
        self.assertIn("1 con error", out.getvalue())
        self.assertIn("1 score(s) creado(s)/actualizado(s)", out.getvalue())

    def test_unknown_symbol_not_in_database_is_warned_and_skipped(self):
        with patch(
            "scoring.management.commands.compute_scores.compute_score",
            return_value=(80, {"sma20": 1.0}),
        ):
            err = StringIO()
            call_command(
                "compute_scores", "--tickers=AAPL,NOEXISTE", stdout=StringIO(), stderr=err
            )

        self.assertIn("NOEXISTE", err.getvalue())
        self.assertTrue(Score.objects.filter(ticker=self.aapl).exists())

    def test_no_active_tickers_reports_nothing_to_process(self):
        Ticker.objects.all().delete()

        out = StringIO()
        call_command("compute_scores", stdout=out)

        self.assertIn("No hay tickers para procesar", out.getvalue())
