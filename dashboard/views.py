"""
Vistas server-side del dashboard. Llaman a los servicios existentes
directo (market_data.services, scoring.services, backtesting.services)
-- nunca hacen una request HTTP interna a api/. Ver CLAUDE.md, sección
"Frontend", para la única excepción (el botón "Explicar" en
ticker_detail.html, que sí llama por fetch() a
GET /api/scores/<symbol>/explain/ para mantener el throttling de Gemini
como único punto de control de cuota).
"""

import csv

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify
from django.utils.timezone import localdate
from django.views import View

from backtesting.services import DEFAULT_HORIZON_DAYS, run_backtest
from market_data.models import Ticker, Watchlist, WatchlistItem
from market_data.services import PriceLoadError, load_prices_for_ticker
from scoring.indicators import sma
from scoring.services import compute_score

from .forms import TickerAddForm, WatchlistForm
from .presentation import score_band_class

CSV_COLUMNS = [
    "Ticker",
    "Nombre",
    "Score",
    "Fecha",
    "Tendencia",
    "Momentum",
    "Volumen",
    "RSI14",
    "SMA20",
    "SMA50",
    "ATR14",
    "VolRelativo",
]

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
                        "item_id": item.id,
                        "ticker": ticker,
                        "score": latest_score,
                        "band_class": score_band_class(
                            latest_score.score if latest_score else None
                        ),
                        "is_best_setup": False,
                    }
                )

            # Ranking: score descendente, sin score al final (nunca None
            # comparado contra int) -- empate desempatado por símbolo para
            # que el orden sea determinístico.
            tickers_data.sort(
                key=lambda row: (
                    row["score"] is None,
                    -(row["score"].score if row["score"] else 0),
                    row["ticker"].symbol,
                )
            )

            # "★ Mejor setup" solo en el primero de la lista (ya ordenada) y
            # solo si su banda es band-top (>=80, bucket_edges de
            # backtesting.services) -- nunca en el "mejor de los peores".
            if tickers_data and tickers_data[0]["band_class"] == "band-top":
                tickers_data[0]["is_best_setup"] = True

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


class WatchlistCreateView(View):
    template_name = "dashboard/watchlist_form.html"

    def get(self, request):
        return render(request, self.template_name, {"form": WatchlistForm()})

    def post(self, request):
        form = WatchlistForm(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form})

        watchlist = form.save()
        messages.success(request, f"Watchlist '{watchlist.name}' creado.")
        return redirect("dashboard:dashboard")


class WatchlistDeleteView(View):
    """
    Acción destructiva: GET muestra una confirmación explícita (nombre +
    cantidad de tickers), recién el POST borra. Nunca un solo botón sin
    paso intermedio.
    """

    template_name = "dashboard/watchlist_confirm_delete.html"

    def get(self, request, pk):
        watchlist = get_object_or_404(Watchlist, pk=pk)
        context = {"watchlist": watchlist, "ticker_count": watchlist.items.count()}
        return render(request, self.template_name, context)

    def post(self, request, pk):
        watchlist = get_object_or_404(Watchlist, pk=pk)
        name = watchlist.name
        watchlist.delete()
        messages.success(request, f"Watchlist '{name}' eliminado.")
        return redirect("dashboard:dashboard")


class WatchlistTickerAddView(View):
    template_name = "dashboard/watchlist_ticker_add.html"

    def get(self, request, pk):
        watchlist = get_object_or_404(Watchlist, pk=pk)
        form = TickerAddForm()
        return render(request, self.template_name, {"watchlist": watchlist, "form": form})

    def post(self, request, pk):
        watchlist = get_object_or_404(Watchlist, pk=pk)
        form = TickerAddForm(request.POST)

        if not form.is_valid():
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"{field}: {error}")
            return redirect("dashboard:dashboard")

        symbol = form.cleaned_data["symbol"]
        ticker, _ = Ticker.objects.get_or_create(
            symbol=symbol,
            defaults={
                "name": form.cleaned_data["name"],
                "exchange": form.cleaned_data["exchange"],
            },
        )

        _, item_created = WatchlistItem.objects.get_or_create(
            watchlist=watchlist, ticker=ticker
        )
        if item_created:
            messages.success(request, f"'{ticker.symbol}' agregado a '{watchlist.name}'.")
        else:
            messages.error(request, f"'{ticker.symbol}' ya está en '{watchlist.name}'.")

        return redirect("dashboard:dashboard")


class WatchlistTickerRemoveView(View):
    """
    Remover un ticker del watchlist no borra ningún dato (Ticker,
    PriceBar, Score siguen existiendo) -- solo el link WatchlistItem, que
    se puede volver a crear agregando el ticker de nuevo. Por eso no
    necesita una página de confirmación aparte, a diferencia de borrar el
    watchlist entero.
    """

    def post(self, request, pk, item_id):
        item = get_object_or_404(WatchlistItem, pk=item_id, watchlist_id=pk)
        symbol = item.ticker.symbol
        watchlist_name = item.watchlist.name
        item.delete()
        messages.success(request, f"'{symbol}' quitado de '{watchlist_name}'.")
        return redirect("dashboard:dashboard")


class WatchlistExportView(View):
    """Sin template: arma el CSV directo como HttpResponse."""

    def get(self, request, pk):
        watchlist = get_object_or_404(Watchlist, pk=pk)
        tickers = (
            Ticker.objects.filter(watchlist_items__watchlist=watchlist)
            .order_by("symbol")
            .prefetch_related("scores")
        )

        filename = f"watchlist_{slugify(watchlist.name)}_{localdate().isoformat()}.csv"
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'

        writer = csv.writer(response)
        writer.writerow(CSV_COLUMNS)

        for ticker in tickers:
            scores = ticker.scores.all()
            latest_score = scores[0] if scores else None

            if latest_score is None:
                writer.writerow([ticker.symbol, ticker.name, "", "", "", "", "", "", "", "", "", ""])
                continue

            components = latest_score.components or {}
            writer.writerow(
                [
                    ticker.symbol,
                    ticker.name,
                    latest_score.score,
                    latest_score.date,
                    components.get("trend_points", ""),
                    components.get("momentum_points", ""),
                    components.get("volume_points", ""),
                    components.get("rsi14", ""),
                    components.get("sma20", ""),
                    components.get("sma50", ""),
                    components.get("atr14", ""),
                    components.get("relative_volume", ""),
                ]
            )

        return response
