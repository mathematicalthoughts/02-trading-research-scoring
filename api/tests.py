"""
Tests de los endpoints de watchlist. GET /api/watchlist/ es lectura
simple (sin mocks, datos reales en DB). POST .../refresh/ mockea
load_prices_for_ticker y compute_score -- su propia lógica ya tiene
test suite en market_data/ y scoring/, acá solo se prueba que la vista
los llama bien y arma el resumen correcto.
"""

from unittest.mock import patch

from rest_framework import status
from rest_framework.test import APITestCase

from market_data.models import Ticker, Watchlist, WatchlistItem
from market_data.services import PriceLoadError


class WatchlistListViewTests(APITestCase):
    def test_lists_watchlists_with_their_tickers(self):
        aapl = Ticker.objects.create(symbol="AAPL", name="Apple Inc.")
        msft = Ticker.objects.create(symbol="MSFT", name="Microsoft Corp.")
        watchlist = Watchlist.objects.create(name="Tech")
        WatchlistItem.objects.create(watchlist=watchlist, ticker=aapl)
        WatchlistItem.objects.create(watchlist=watchlist, ticker=msft)
        Watchlist.objects.create(name="Vacía")

        response = self.client.get("/api/watchlist/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2)

        tech = next(w for w in response.data if w["name"] == "Tech")
        symbols = {t["symbol"] for t in tech["tickers"]}
        self.assertEqual(symbols, {"AAPL", "MSFT"})

        empty = next(w for w in response.data if w["name"] == "Vacía")
        self.assertEqual(empty["tickers"], [])


class WatchlistRefreshViewTests(APITestCase):
    def setUp(self):
        self.aapl = Ticker.objects.create(symbol="AAPL", name="Apple Inc.")
        self.msft = Ticker.objects.create(symbol="MSFT", name="Microsoft Corp.")
        self.watchlist = Watchlist.objects.create(name="Tech")
        WatchlistItem.objects.create(watchlist=self.watchlist, ticker=self.aapl)
        WatchlistItem.objects.create(watchlist=self.watchlist, ticker=self.msft)

    def test_unknown_watchlist_returns_404(self):
        response = self.client.post("/api/watchlist/9999/refresh/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_calls_load_prices_and_compute_score_once_per_ticker(self):
        def fake_compute_score(ticker):
            if ticker.symbol == "AAPL":
                return 70, {"trend_points": 40}
            return None, "sin histórico suficiente"

        with patch("api.views.load_prices_for_ticker") as mock_load, patch(
            "api.views.compute_score", side_effect=fake_compute_score
        ) as mock_compute:
            response = self.client.post(f"/api/watchlist/{self.watchlist.pk}/refresh/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(mock_load.call_count, 2)
        self.assertEqual(mock_compute.call_count, 2)
        mock_load.assert_any_call(self.aapl, days=90)
        mock_load.assert_any_call(self.msft, days=90)

        self.assertEqual(response.data["tickers_procesados"], 2)
        self.assertEqual(response.data["errores_de_precio"], 0)
        self.assertEqual(response.data["scores_actualizados"], 1)
        self.assertEqual(response.data["omitidos_por_falta_de_historico"], 1)

    def test_price_error_on_one_ticker_does_not_break_the_rest(self):
        def fake_load(ticker, days):
            if ticker.symbol == "AAPL":
                raise PriceLoadError("boom")

        with patch("api.views.load_prices_for_ticker", side_effect=fake_load), patch(
            "api.views.compute_score", return_value=(80, {})
        ) as mock_compute:
            response = self.client.post(f"/api/watchlist/{self.watchlist.pk}/refresh/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["tickers_procesados"], 2)
        self.assertEqual(response.data["errores_de_precio"], 1)
        self.assertEqual(response.data["scores_actualizados"], 1)
        # compute_score no se llama para el ticker que falló al traer precio.
        mock_compute.assert_called_once_with(self.msft)
