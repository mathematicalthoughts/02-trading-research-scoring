"""
Vistas server-side del dashboard. Llaman a los servicios existentes
directo (market_data.services, scoring.services, backtesting.services)
-- nunca hacen una request HTTP interna a api/. Ver CLAUDE.md, sección
"Frontend", para la única excepción (el botón "Explicar" en
ticker_detail.html, que sí llama por fetch() a
GET /api/scores/<symbol>/explain/ para mantener el throttling de Gemini
como único punto de control de cuota).
"""

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from backtesting.services import DEFAULT_HORIZON_DAYS, run_backtest
from market_data.models import Ticker, Watchlist
from market_data.services import PriceLoadError, load_prices_for_ticker
from scoring.indicators import sma
from scoring.services import compute_score

from .presentation import score_band_class

# Mismo valor que api/views.py::WatchlistRefreshView.REFRESH_DAYS --
# duplicado a propósito: la vista DRF no se toca (ver CLAUDE.md).
REFRESH_DAYS = 90

CHART_HISTORY_BARS = 200
CHART_DISPLAY_BARS = 60


def _price_chart_data(ticker):
    """
    Últimos CHART_DISPLAY_BARS PriceBar con su serie de SMA20/SMA50 para
    graficar -- se traen CHART_HISTORY_BARS bars (más de los que se
    muestran) para que la SMA50 del primer punto visible ya tenga
    suficiente historia previa y no arranque en None. `sma()` se reusa
    tal cual de scoring/indicators.py, llamada una vez por día (mismo
    patrón que backtesting/services.py con _compute_score_from_bars).
    """
    bars = list(reversed(ticker.price_bars.order_by("-date")[:CHART_HISTORY_BARS]))
    if not bars:
        return None

    closes = [float(b.close) for b in bars]
    sma20_series = [sma(closes[: i + 1], 20) for i in range(len(closes))]
    sma50_series = [sma(closes[: i + 1], 50) for i in range(len(closes))]

    offset = max(0, len(bars) - CHART_DISPLAY_BARS)
    display_bars = bars[offset:]

    return {
        "labels": [bar.date.isoformat() for bar in display_bars],
        "close": closes[offset:],
        "sma20": sma20_series[offset:],
        "sma50": sma50_series[offset:],
    }


class DashboardView(View):
    template_name = "dashboard/dashboard.html"

    def get(self, request):
        # Mismo patrón de prefetch_related que api/views.py::WatchlistListView,
        # extendido con "__scores" para traer el Score más reciente de cada
        # ticker sin N+1 (Score.Meta.ordering = ["-date"], así que
        # ticker.scores.all()[0], sobre el queryset ya prefetcheado, no
        # dispara una query nueva).
        watchlists = Watchlist.objects.prefetch_related("items__ticker__scores")

        watchlists_data = []
        for watchlist in watchlists:
            tickers_data = []
            for item in watchlist.items.all():
                ticker = item.ticker
                scores = ticker.scores.all()
                latest_score = scores[0] if scores else None
                tickers_data.append(
                    {
                        "ticker": ticker,
                        "score": latest_score,
                        "band_class": score_band_class(
                            latest_score.score if latest_score else None
                        ),
                    }
                )
            watchlists_data.append({"watchlist": watchlist, "tickers": tickers_data})

        return render(request, self.template_name, {"watchlists": watchlists_data})

    def post(self, request):
        """
        "Refrescar precios": misma lógica que
        api/views.py::WatchlistRefreshView, pero invocando
        load_prices_for_ticker/compute_score directo (no la vista DRF) --
        ver decisión de arquitectura en CLAUDE.md.
        """
        watchlist = get_object_or_404(Watchlist, pk=request.POST.get("watchlist_id"))
        tickers = Ticker.objects.filter(watchlist_items__watchlist=watchlist)

        processed = 0
        price_errors = 0
        scores_updated = 0
        skipped_insufficient_history = 0

        for ticker in tickers:
            processed += 1
            try:
                load_prices_for_ticker(ticker, days=REFRESH_DAYS)
            except PriceLoadError:
                price_errors += 1
                continue

            score, _ = compute_score(ticker)
            if score is None:
                skipped_insufficient_history += 1
            else:
                scores_updated += 1

        messages.success(
            request,
            f"'{watchlist.name}': {processed} ticker(s) procesado(s), "
            f"{price_errors} con error de precio, {scores_updated} score(s) "
            f"actualizado(s), {skipped_insufficient_history} omitido(s) por "
            "falta de histórico.",
        )
        return redirect("dashboard:dashboard")


class TickerDetailView(View):
    template_name = "dashboard/ticker_detail.html"

    def get(self, request, symbol):
        ticker = get_object_or_404(Ticker, symbol=symbol.upper())
        latest_score = ticker.scores.first()

        explanation = None
        if latest_score is not None:
            # Si ya existe una AgentExplanation para el Score vigente, se
            # muestra de entrada -- no se gasta cupo de Gemini de más.
            explanation = latest_score.explanations.first()

        context = {
            "ticker": ticker,
            "latest_score": latest_score,
            "score_band_class": score_band_class(
                latest_score.score if latest_score else None
            ),
            "explanation": explanation,
            "chart_data": _price_chart_data(ticker),
        }
        return render(request, self.template_name, context)


class BacktestView(View):
    template_name = "dashboard/backtest.html"

    def get(self, request, symbol):
        ticker = get_object_or_404(Ticker, symbol=symbol.upper())

        try:
            horizon_days = int(request.GET.get("horizon_days", DEFAULT_HORIZON_DAYS))
        except (TypeError, ValueError):
            horizon_days = DEFAULT_HORIZON_DAYS
        horizon_days = max(1, horizon_days)

        result = run_backtest(ticker, horizon_days=horizon_days)

        chart_data = None
        if "buckets" in result and result["buckets"]:
            chart_data = {
                "labels": list(result["buckets"].keys()),
                "avg_return_pct": [
                    bucket["avg_return_pct"] for bucket in result["buckets"].values()
                ],
            }

        context = {
            "ticker": ticker,
            "horizon_days": horizon_days,
            "result": result,
            "chart_data": chart_data,
        }
        return render(request, self.template_name, context)
