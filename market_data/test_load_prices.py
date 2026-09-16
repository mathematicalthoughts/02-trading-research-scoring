"""
Tests del management command load_prices. Nunca pega a la red real de
yfinance: mockea market_data.services.yf.Ticker para cada símbolo.
"""

from io import StringIO
from unittest.mock import MagicMock, patch

import pandas as pd
from django.core.management import call_command
from django.test import TestCase

from .models import PriceBar, Ticker


def _history_df(rows):
    index = pd.to_datetime([r["date"] for r in rows])
    return pd.DataFrame(
        {
            "Open": [r["open"] for r in rows],
            "High": [r["high"] for r in rows],
            "Low": [r["low"] for r in rows],
            "Close": [r["close"] for r in rows],
            "Volume": [r["volume"] for r in rows],
        },
        index=index,
    )


def _mock_yf_ticker(responses):
    """
    `responses` mapea symbol -> DataFrame (histórico simulado) o Exception
    (para simular un símbolo que yfinance no puede resolver / error de red).
    """

    def _factory(symbol):
        mock_ticker = MagicMock()
        response = responses[symbol]
        if isinstance(response, Exception):
            mock_ticker.history.side_effect = response
        else:
            mock_ticker.history.return_value = response
        return mock_ticker

    return _factory


AAPL_HISTORY = _history_df(
    [
        {
            "date": "2026-09-01",
            "open": 150.0,
            "high": 151.5,
            "low": 149.0,
            "close": 150.75,
            "volume": 1_000_000,
        },
        {
            "date": "2026-09-02",
            "open": 150.8,
            "high": 152.0,
            "low": 150.0,
            "close": 151.9,
            "volume": 1_100_000,
        },
    ]
)


class LoadPricesCommandTests(TestCase):
    def setUp(self):
        self.aapl = Ticker.objects.create(symbol="AAPL", name="Apple Inc.")

    def test_successful_load_creates_price_bars(self):
        with patch(
            "market_data.services.yf.Ticker",
            side_effect=_mock_yf_ticker({"AAPL": AAPL_HISTORY}),
        ):
            out = StringIO()
            call_command("load_prices", "--tickers=AAPL", "--days=5", stdout=out)

        self.assertEqual(PriceBar.objects.filter(ticker=self.aapl).count(), 2)
        bar = PriceBar.objects.get(ticker=self.aapl, date="2026-09-01")
        self.assertEqual(bar.volume, 1_000_000)
        self.assertIn("1 ticker(s) procesado(s)", out.getvalue())
        self.assertIn("0 con error", out.getvalue())
        self.assertIn("2 fila(s) de PriceBar", out.getvalue())

    def test_unresolvable_ticker_does_not_break_the_run(self):
        bad = Ticker.objects.create(symbol="ZZZINVALID", name="Ticker inválido")
        responses = {
            "AAPL": AAPL_HISTORY,
            "ZZZINVALID": pd.DataFrame(),  # yfinance devuelve vacío para símbolos inválidos
        }

        with patch("market_data.services.yf.Ticker", side_effect=_mock_yf_ticker(responses)):
            out = StringIO()
            err = StringIO()
            call_command(
                "load_prices",
                "--tickers=AAPL,ZZZINVALID",
                "--days=5",
                stdout=out,
                stderr=err,
            )

        # El ticker válido sí se cargó pese al error del otro.
        self.assertEqual(PriceBar.objects.filter(ticker=self.aapl).count(), 2)
        self.assertEqual(PriceBar.objects.filter(ticker=bad).count(), 0)

        self.assertIn("FAIL ZZZINVALID", err.getvalue())
        self.assertIn("2 ticker(s) procesado(s)", out.getvalue())
        self.assertIn("1 con error", out.getvalue())
        self.assertIn("2 fila(s) de PriceBar", out.getvalue())

    def test_running_twice_is_idempotent(self):
        with patch(
            "market_data.services.yf.Ticker",
            side_effect=_mock_yf_ticker({"AAPL": AAPL_HISTORY}),
        ):
            call_command("load_prices", "--tickers=AAPL", "--days=5", stdout=StringIO())
            call_command("load_prices", "--tickers=AAPL", "--days=5", stdout=StringIO())

        self.assertEqual(PriceBar.objects.filter(ticker=self.aapl).count(), 2)

    def test_no_tickers_flag_uses_active_tickers_only(self):
        Ticker.objects.create(symbol="MSFT", name="Microsoft Corp.", active=False)

        with patch(
            "market_data.services.yf.Ticker",
            side_effect=_mock_yf_ticker({"AAPL": AAPL_HISTORY}),
        ) as mock_ticker_cls:
            call_command("load_prices", "--days=5", stdout=StringIO())

        mock_ticker_cls.assert_called_once_with("AAPL")

    def test_network_error_is_caught_and_reported(self):
        responses = {"AAPL": ConnectionError("timeout hablando con yfinance")}

        with patch("market_data.services.yf.Ticker", side_effect=_mock_yf_ticker(responses)):
            out = StringIO()
            err = StringIO()
            call_command(
                "load_prices", "--tickers=AAPL", "--days=5", stdout=out, stderr=err
            )

        self.assertEqual(PriceBar.objects.filter(ticker=self.aapl).count(), 0)
        self.assertIn("FAIL AAPL", err.getvalue())
        self.assertIn("1 con error", out.getvalue())

    def test_unknown_symbol_not_in_database_is_warned_and_skipped(self):
        with patch(
            "market_data.services.yf.Ticker",
            side_effect=_mock_yf_ticker({"AAPL": AAPL_HISTORY}),
        ):
            out = StringIO()
            err = StringIO()
            call_command(
                "load_prices",
                "--tickers=AAPL,NOEXISTE",
                "--days=5",
                stdout=out,
                stderr=err,
            )

        self.assertIn("NOEXISTE", err.getvalue())
        self.assertIn("1 ticker(s) procesado(s)", out.getvalue())

    def test_no_active_tickers_reports_nothing_to_process(self):
        Ticker.objects.all().delete()

        out = StringIO()
        call_command("load_prices", "--days=5", stdout=out)

        self.assertIn("No hay tickers para procesar", out.getvalue())
