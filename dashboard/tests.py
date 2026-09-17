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

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from agent.models import AgentExplanation
from market_data.models import PriceBar, Ticker, Watchlist, WatchlistItem
from market_data.services import PriceLoadError, TickerMetadataError
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
        mock_load.assert_any_call(self.aapl, days=500)
        mock_load.assert_any_call(self.msft, days=500)

        messages = [str(m) for m in response.context["messages"]]
        self.assertEqual(len(messages), 1)
        self.assertIn("2 ticker(s) procesado(s)", messages[0])
        self.assertIn("1 score(s) actualizado(s)", messages[0])
        self.assertIn("1 omitido(s) por falta de histórico", messages[0])

        # Regresión: compute_score(ticker) solo CALCULA, no persiste --
        # "Refrescar precios" nunca guardaba el Score hasta este fix
        # (encontrado corriendo el flujo real contra yfinance, no con
        # pytest). El resumen decía "actualizado" sin haber guardado nada.
        saved_score = Score.objects.get(ticker=self.aapl)
        self.assertEqual(saved_score.score, 70)
        self.assertEqual(saved_score.date, datetime.date.today())
        self.assertFalse(Score.objects.filter(ticker=self.msft).exists())

    def test_refresh_post_is_idempotent_for_scores(self):
        with patch("dashboard.views.load_prices_for_ticker"), patch(
            "dashboard.views.compute_score", return_value=(70, {"trend_points": 40})
        ):
            self.client.post(
                reverse("dashboard:dashboard"), {"watchlist_id": self.watchlist.pk}
            )
            self.client.post(
                reverse("dashboard:dashboard"), {"watchlist_id": self.watchlist.pk}
            )

        # Correr el refresh dos veces el mismo día actualiza, no duplica.
        self.assertEqual(Score.objects.filter(ticker=self.aapl).count(), 1)
        self.assertEqual(Score.objects.get(ticker=self.aapl).score, 70)

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

    def test_price_column_shows_latest_close_or_dash(self):
        _make_bar(self.aapl, 0, close=150)
        _make_bar(self.aapl, 1, close=155)  # más reciente por fecha

        response = self.client.get(reverse("dashboard:dashboard"))

        rows_by_symbol = {
            row["ticker"].symbol: row
            for row in response.context["watchlists"][0]["tickers"]
        }
        self.assertEqual(rows_by_symbol["AAPL"]["latest_price"].close, Decimal("155.0000"))
        self.assertIsNone(rows_by_symbol["MSFT"]["latest_price"])
        self.assertContains(response, "$155.00")

    def test_no_n_plus_1_when_watchlist_has_more_tickers(self):
        # Regresión: agregar "items__ticker__price_bars" al
        # prefetch_related no debe convertir esto en N+1 -- la cantidad
        # de queries tiene que ser la misma con 2 tickers que con 4.
        with CaptureQueriesContext(connection) as ctx_two_tickers:
            self.client.get(reverse("dashboard:dashboard"))

        googl = Ticker.objects.create(symbol="GOOGL", name="Alphabet Inc.")
        tsla = Ticker.objects.create(symbol="TSLA", name="Tesla Inc.")
        WatchlistItem.objects.create(watchlist=self.watchlist, ticker=googl)
        WatchlistItem.objects.create(watchlist=self.watchlist, ticker=tsla)
        _make_bar(googl, 0, close=100)
        _make_bar(tsla, 0, close=200)

        with CaptureQueriesContext(connection) as ctx_four_tickers:
            self.client.get(reverse("dashboard:dashboard"))

        self.assertEqual(
            len(ctx_two_tickers.captured_queries), len(ctx_four_tickers.captured_queries)
        )

    def test_tickers_are_ranked_by_score_descending_with_none_last(self):
        googl = Ticker.objects.create(symbol="GOOGL", name="Alphabet Inc.")
        WatchlistItem.objects.create(watchlist=self.watchlist, ticker=googl)
        # MSFT queda sin Score (no se crea ninguno).
        Score.objects.create(
            ticker=self.aapl, date=datetime.date(2026, 1, 1), score=55, components={}
        )
        Score.objects.create(
            ticker=googl, date=datetime.date(2026, 1, 1), score=90, components={}
        )

        response = self.client.get(reverse("dashboard:dashboard"))

        symbols_in_order = [
            row["ticker"].symbol for row in response.context["watchlists"][0]["tickers"]
        ]
        self.assertEqual(symbols_in_order, ["GOOGL", "AAPL", "MSFT"])

    def test_best_setup_indicator_only_when_top_score_is_band_top(self):
        Score.objects.create(
            ticker=self.aapl, date=datetime.date(2026, 1, 1), score=90, components={}
        )
        Score.objects.create(
            ticker=self.msft, date=datetime.date(2026, 1, 1), score=70, components={}
        )

        response = self.client.get(reverse("dashboard:dashboard"))

        rows = response.context["watchlists"][0]["tickers"]
        self.assertTrue(rows[0]["is_best_setup"])
        self.assertEqual(rows[0]["ticker"].symbol, "AAPL")
        self.assertFalse(rows[1]["is_best_setup"])
        self.assertContains(response, "Mejor setup")

    def test_no_best_setup_indicator_when_no_ticker_reaches_band_top(self):
        Score.objects.create(
            ticker=self.aapl, date=datetime.date(2026, 1, 1), score=70, components={}
        )

        response = self.client.get(reverse("dashboard:dashboard"))

        rows = response.context["watchlists"][0]["tickers"]
        self.assertFalse(any(row["is_best_setup"] for row in rows))
        self.assertNotContains(response, "Mejor setup")


class WatchlistCreateViewTests(TestCase):
    def test_get_shows_empty_form(self):
        response = self.client.get(reverse("dashboard:watchlist-create"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "dashboard/watchlist_form.html")

    def test_post_creates_watchlist_and_redirects(self):
        response = self.client.post(
            reverse("dashboard:watchlist-create"), {"name": "Semiconductors"}, follow=True
        )

        self.assertTrue(Watchlist.objects.filter(name="Semiconductors").exists())
        self.assertRedirects(response, reverse("dashboard:dashboard"))
        messages = [str(m) for m in response.context["messages"]]
        self.assertIn("'Semiconductors' creado", messages[0])

    def test_post_with_blank_name_reshows_form_with_errors(self):
        response = self.client.post(reverse("dashboard:watchlist-create"), {"name": ""})

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Watchlist.objects.exists())
        self.assertFalse(response.context["form"].is_valid())

    def test_post_with_duplicate_name_reshows_form_with_errors(self):
        Watchlist.objects.create(name="Tech")

        response = self.client.post(reverse("dashboard:watchlist-create"), {"name": "Tech"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Watchlist.objects.filter(name="Tech").count(), 1)


class WatchlistDeleteViewTests(TestCase):
    def setUp(self):
        self.watchlist = Watchlist.objects.create(name="Tech")
        ticker = Ticker.objects.create(symbol="AAPL", name="Apple Inc.")
        WatchlistItem.objects.create(watchlist=self.watchlist, ticker=ticker)

    def test_get_shows_confirmation_and_does_not_delete(self):
        response = self.client.get(
            reverse("dashboard:watchlist-delete", args=[self.watchlist.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "dashboard/watchlist_confirm_delete.html")
        self.assertTrue(Watchlist.objects.filter(pk=self.watchlist.pk).exists())
        self.assertEqual(response.context["ticker_count"], 1)
        self.assertContains(response, "Tech")

    def test_post_deletes_watchlist(self):
        response = self.client.post(
            reverse("dashboard:watchlist-delete", args=[self.watchlist.pk]), follow=True
        )

        self.assertFalse(Watchlist.objects.filter(pk=self.watchlist.pk).exists())
        self.assertRedirects(response, reverse("dashboard:dashboard"))

    def test_get_unknown_watchlist_returns_404(self):
        response = self.client.get(reverse("dashboard:watchlist-delete", args=[9999]))
        self.assertEqual(response.status_code, 404)


class WatchlistTickerAddViewTests(TestCase):
    """
    fetch_ticker_metadata (yfinance) siempre mockeado -- nunca le pega a
    la red real en tests. Su propia lógica ya tiene test suite en
    market_data/test_services.py.
    """

    def setUp(self):
        self.watchlist = Watchlist.objects.create(name="Tech")

    def test_get_shows_form(self):
        response = self.client.get(
            reverse("dashboard:watchlist-ticker-add", args=[self.watchlist.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "dashboard/watchlist_ticker_add.html")

    def test_post_creates_new_ticker_with_metadata_from_yfinance(self):
        with patch(
            "dashboard.views.fetch_ticker_metadata",
            return_value={"name": "Apple Inc.", "exchange": "NASDAQ"},
        ) as mock_fetch:
            response = self.client.post(
                reverse("dashboard:watchlist-ticker-add", args=[self.watchlist.pk]),
                {"symbol": "aapl"},
                follow=True,
            )

        mock_fetch.assert_called_once_with("AAPL")
        ticker = Ticker.objects.get(symbol="AAPL")
        self.assertEqual(ticker.name, "Apple Inc.")
        self.assertEqual(ticker.exchange, "NASDAQ")
        self.assertTrue(
            WatchlistItem.objects.filter(watchlist=self.watchlist, ticker=ticker).exists()
        )
        messages = [str(m) for m in response.context["messages"]]
        self.assertIn("agregado a 'Tech'", messages[0])

    def test_post_invalid_symbol_creates_nothing_and_shows_error(self):
        with patch(
            "dashboard.views.fetch_ticker_metadata",
            side_effect=TickerMetadataError("'ZZZINVALID' no parece un ticker válido."),
        ):
            response = self.client.post(
                reverse("dashboard:watchlist-ticker-add", args=[self.watchlist.pk]),
                {"symbol": "ZZZINVALID"},
                follow=True,
            )

        self.assertFalse(Ticker.objects.exists())
        self.assertFalse(WatchlistItem.objects.exists())
        messages = [str(m) for m in response.context["messages"]]
        self.assertIn("No encontramos ese ticker", messages[0])

    def test_post_reuses_existing_ticker_by_symbol_without_overwriting_it(self):
        existing = Ticker.objects.create(symbol="AAPL", name="Apple Inc. (original)")

        with patch("dashboard.views.fetch_ticker_metadata") as mock_fetch:
            mock_fetch.return_value = {"name": "Otro nombre", "exchange": "NASDAQ"}
            self.client.post(
                reverse("dashboard:watchlist-ticker-add", args=[self.watchlist.pk]),
                {"symbol": "AAPL"},
            )

        self.assertEqual(Ticker.objects.filter(symbol="AAPL").count(), 1)
        existing.refresh_from_db()
        self.assertEqual(existing.name, "Apple Inc. (original)")  # get_or_create no pisa el existente

    def test_post_duplicate_in_same_watchlist_shows_error_without_duplicating(self):
        ticker = Ticker.objects.create(symbol="AAPL", name="Apple Inc.")
        WatchlistItem.objects.create(watchlist=self.watchlist, ticker=ticker)

        with patch(
            "dashboard.views.fetch_ticker_metadata",
            return_value={"name": "Apple Inc.", "exchange": "NASDAQ"},
        ):
            response = self.client.post(
                reverse("dashboard:watchlist-ticker-add", args=[self.watchlist.pk]),
                {"symbol": "AAPL"},
                follow=True,
            )

        self.assertEqual(
            WatchlistItem.objects.filter(watchlist=self.watchlist, ticker=ticker).count(), 1
        )
        messages = [str(m) for m in response.context["messages"]]
        self.assertIn("ya está en 'Tech'", messages[0])

    def test_post_invalid_form_shows_error_message(self):
        response = self.client.post(
            reverse("dashboard:watchlist-ticker-add", args=[self.watchlist.pk]),
            {"symbol": ""},
            follow=True,
        )

        self.assertFalse(Ticker.objects.exists())
        messages = [str(m) for m in response.context["messages"]]
        self.assertTrue(len(messages) >= 1)

    def test_post_unknown_watchlist_returns_404(self):
        response = self.client.post(
            reverse("dashboard:watchlist-ticker-add", args=[9999]), {"symbol": "AAPL"}
        )
        self.assertEqual(response.status_code, 404)


class WatchlistTickerRemoveViewTests(TestCase):
    def setUp(self):
        self.watchlist = Watchlist.objects.create(name="Tech")
        self.ticker = Ticker.objects.create(symbol="AAPL", name="Apple Inc.")
        self.item = WatchlistItem.objects.create(watchlist=self.watchlist, ticker=self.ticker)

    def test_post_removes_the_item_but_keeps_the_ticker(self):
        response = self.client.post(
            reverse(
                "dashboard:watchlist-ticker-remove",
                args=[self.watchlist.pk, self.item.pk],
            ),
            follow=True,
        )

        self.assertFalse(WatchlistItem.objects.filter(pk=self.item.pk).exists())
        self.assertTrue(Ticker.objects.filter(pk=self.ticker.pk).exists())
        self.assertRedirects(response, reverse("dashboard:dashboard"))
        messages = [str(m) for m in response.context["messages"]]
        self.assertIn("quitado de 'Tech'", messages[0])

    def test_get_is_not_allowed(self):
        response = self.client.get(
            reverse(
                "dashboard:watchlist-ticker-remove",
                args=[self.watchlist.pk, self.item.pk],
            )
        )
        self.assertEqual(response.status_code, 405)

    def test_unknown_item_returns_404(self):
        response = self.client.post(
            reverse("dashboard:watchlist-ticker-remove", args=[self.watchlist.pk, 9999])
        )
        self.assertEqual(response.status_code, 404)


class WatchlistExportViewTests(TestCase):
    def setUp(self):
        self.watchlist = Watchlist.objects.create(name="Tech")
        self.aapl = Ticker.objects.create(symbol="AAPL", name="Apple Inc.")
        self.msft = Ticker.objects.create(symbol="MSFT", name="Microsoft Corp.")
        WatchlistItem.objects.create(watchlist=self.watchlist, ticker=self.aapl)
        WatchlistItem.objects.create(watchlist=self.watchlist, ticker=self.msft)
        # MSFT queda sin Score a propósito.
        Score.objects.create(
            ticker=self.aapl,
            date=datetime.date(2026, 1, 2),
            score=70,
            components={
                "trend_points": 40,
                "momentum_points": 30,
                "volume_points": 0,
                "rsi14": 61.37,
                "sma20": 319.27,
                "sma50": 318.70,
                "atr14": 7.55,
                "relative_volume": 0.74,
            },
        )

    def test_content_type_and_filename(self):
        response = self.client.get(
            reverse("dashboard:watchlist-export", args=[self.watchlist.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        self.assertIn("attachment; filename=", response["Content-Disposition"])
        self.assertIn("watchlist_tech_", response["Content-Disposition"])
        self.assertIn(".csv", response["Content-Disposition"])

    def test_csv_content_includes_header_scored_and_unscored_rows(self):
        response = self.client.get(
            reverse("dashboard:watchlist-export", args=[self.watchlist.pk])
        )
        content = response.content.decode()
        rows = content.strip().splitlines()

        self.assertEqual(
            rows[0],
            "Ticker,Nombre,Score,Fecha,Tendencia,Momentum,Volumen,RSI14,SMA20,SMA50,ATR14,VolRelativo",
        )
        # AAPL (con score): orden alfabético, AAPL antes que MSFT.
        self.assertIn("AAPL,Apple Inc.,70,2026-01-02,40,30,0,61.37,319.27,318.7,7.55,0.74", rows[1])
        # MSFT (sin score): la fila existe, con las columnas de score vacías.
        self.assertEqual(rows[2], "MSFT,Microsoft Corp.,,,,,,,,,,")

    def test_unknown_watchlist_returns_404(self):
        response = self.client.get(reverse("dashboard:watchlist-export", args=[9999]))
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

    def test_sma50_has_no_none_values_with_enough_warmup_history(self):
        """
        Regresión: con menos de ~110 barras (CHART_DISPLAY_BARS=60 +
        50 de warm-up previo que necesita SMA50), la SMA50 aparecía
        cortada (None) en los primeros puntos visibles del chart --
        causa raíz: REFRESH_DAYS=90 solo cargaba ~80-90 barras. Con 350
        barras (REFRESH_DAYS=500) alcanza con margen.
        """
        for i in range(350):
            _make_bar(self.ticker, i, close=100 + (i % 7) - 3)

        response = self.client.get(
            reverse("dashboard:ticker-detail", args=[self.ticker.symbol])
        )

        sma50_series = response.context["chart_data"]["sma50"]
        self.assertEqual(len(sma50_series), 60)
        self.assertNotIn(None, sma50_series)

    def test_chart_data_is_none_without_price_bars(self):
        response = self.client.get(
            reverse("dashboard:ticker-detail", args=[self.ticker.symbol])
        )

        self.assertIsNone(response.context["chart_data"])
        self.assertNotContains(response, "priceChartData")

    def test_sparkline_trend_is_up_with_ascending_closes(self):
        for i in range(60):
            _make_bar(self.ticker, i, close=100 + i)  # último close > primero

        response = self.client.get(
            reverse("dashboard:ticker-detail", args=[self.ticker.symbol])
        )

        self.assertEqual(response.context["sparkline_trend"], "up")
        self.assertEqual(len(response.context["sparkline_data"]), 20)
        self.assertContains(response, "sparklineData")

    def test_sparkline_trend_is_down_with_descending_closes(self):
        for i in range(60):
            _make_bar(self.ticker, i, close=200 - i)  # último close < primero

        response = self.client.get(
            reverse("dashboard:ticker-detail", args=[self.ticker.symbol])
        )

        self.assertEqual(response.context["sparkline_trend"], "down")

    def test_sparkline_is_none_without_price_bars(self):
        response = self.client.get(
            reverse("dashboard:ticker-detail", args=[self.ticker.symbol])
        )

        self.assertIsNone(response.context["sparkline_data"])
        self.assertIsNone(response.context["sparkline_trend"])
        self.assertNotContains(response, "sparklineData")


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
