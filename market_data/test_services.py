"""
Tests directos de market_data/services.py (no vía el management command,
eso ya está cubierto en test_load_prices.py). Nunca pega a la red real de
yfinance: mockea market_data.services.yf.Ticker.
"""

from unittest.mock import MagicMock, patch

import pandas as pd
from django.test import TestCase

from .models import PriceBar, Ticker
from .services import (
    PriceLoadError,
    TickerMetadataError,
    fetch_ticker_metadata,
    load_prices_for_ticker,
)


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


class LoadPricesForTickerNanHandlingTests(TestCase):
    """
    Regresión: el loop de escritura (incluido int(row["Volume"])) vivía
    fuera del try/except que solo cubría el fetch -- una fila con NaN
    (común en la barra del día en curso) hacía explotar int(NaN) sin
    capturar y se propagaba como 500 en DashboardView.post.
    """

    def setUp(self):
        self.ticker = Ticker.objects.create(symbol="AAPL", name="Apple Inc.")

    def test_row_with_nan_volume_is_skipped_other_rows_are_saved(self):
        history = _history_df(
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
                    "volume": float("nan"),  # ej. barra del día en curso
                },
                {
                    "date": "2026-09-03",
                    "open": 151.0,
                    "high": 153.0,
                    "low": 150.5,
                    "close": 152.5,
                    "volume": 900_000,
                },
            ]
        )
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = history

        with patch("market_data.services.yf.Ticker", return_value=mock_ticker):
            rows = load_prices_for_ticker(self.ticker, days=5)

        self.assertEqual(rows, 2)
        self.assertEqual(PriceBar.objects.filter(ticker=self.ticker).count(), 2)
        self.assertFalse(
            PriceBar.objects.filter(ticker=self.ticker, date="2026-09-02").exists()
        )
        self.assertTrue(
            PriceBar.objects.filter(ticker=self.ticker, date="2026-09-01").exists()
        )
        self.assertTrue(
            PriceBar.objects.filter(ticker=self.ticker, date="2026-09-03").exists()
        )

    def test_row_with_nan_close_is_skipped_without_crashing(self):
        history = _history_df(
            [
                {
                    "date": "2026-09-01",
                    "open": 150.0,
                    "high": 151.5,
                    "low": 149.0,
                    "close": float("nan"),  # ej. dividendo/split sin ajustar todavía
                    "volume": 1_000_000,
                }
            ]
        )
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = history

        with patch("market_data.services.yf.Ticker", return_value=mock_ticker):
            rows = load_prices_for_ticker(self.ticker, days=5)

        self.assertEqual(rows, 0)
        self.assertEqual(PriceBar.objects.count(), 0)

    def test_unexpected_error_during_write_is_wrapped_as_price_load_error(self):
        history = _history_df(
            [
                {
                    "date": "2026-09-01",
                    "open": 150.0,
                    "high": 151.5,
                    "low": 149.0,
                    "close": 150.75,
                    "volume": 1_000_000,
                }
            ]
        )
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = history

        with patch("market_data.services.yf.Ticker", return_value=mock_ticker), patch(
            "market_data.services.PriceBar.objects.update_or_create",
            side_effect=RuntimeError("db explotó"),
        ):
            with self.assertRaises(PriceLoadError):
                load_prices_for_ticker(self.ticker, days=5)


class FetchTickerMetadataTests(TestCase):
    def test_valid_symbol_resolves_name_and_exchange(self):
        mock_ticker = MagicMock()
        mock_ticker.get_info.return_value = {
            "longName": "NVIDIA Corporation",
            "exchange": "NMS",
        }

        with patch(
            "market_data.services.yf.Ticker", return_value=mock_ticker
        ) as mock_ticker_cls:
            metadata = fetch_ticker_metadata("NVDA")

        mock_ticker_cls.assert_called_once_with("NVDA")
        self.assertEqual(metadata, {"name": "NVIDIA Corporation", "exchange": "NMS"})

    def test_falls_back_to_short_name_when_long_name_is_missing(self):
        mock_ticker = MagicMock()
        mock_ticker.get_info.return_value = {"shortName": "NVIDIA", "exchange": "NMS"}

        with patch("market_data.services.yf.Ticker", return_value=mock_ticker):
            metadata = fetch_ticker_metadata("NVDA")

        self.assertEqual(metadata["name"], "NVIDIA")

    def test_missing_exchange_defaults_to_empty_string(self):
        mock_ticker = MagicMock()
        mock_ticker.get_info.return_value = {"longName": "NVIDIA Corporation"}

        with patch("market_data.services.yf.Ticker", return_value=mock_ticker):
            metadata = fetch_ticker_metadata("NVDA")

        self.assertEqual(metadata["exchange"], "")

    def test_invalid_symbol_without_name_raises_ticker_metadata_error(self):
        mock_ticker = MagicMock()
        mock_ticker.get_info.return_value = {}

        with patch("market_data.services.yf.Ticker", return_value=mock_ticker):
            with self.assertRaises(TickerMetadataError):
                fetch_ticker_metadata("ZZZINVALID")

    def test_get_info_raising_is_wrapped_as_ticker_metadata_error(self):
        with patch("market_data.services.yf.Ticker", side_effect=ConnectionError("boom")):
            with self.assertRaises(TickerMetadataError):
                fetch_ticker_metadata("NVDA")
