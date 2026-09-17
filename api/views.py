from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from agent.services import AgentError, explain_and_persist
from market_data.models import Ticker, Watchlist
from market_data.services import PriceLoadError, load_prices_for_ticker
from scoring.services import compute_score

from .serializers import AgentExplanationSerializer, ScoreSerializer, WatchlistSerializer
from .throttling import AgentExplainThrottle

# 500 días calendario ~= 350 barras de trading -- sincronizado en valor
# con dashboard/views.py::REFRESH_DAYS (duplicado a propósito, ver
# CLAUDE.md): con 90 no alcanzaba para el warm-up de SMA50 del chart ni
# para backtests con horizon_days > ~30 sesiones.
REFRESH_DAYS = 500


class WatchlistListView(APIView):
    def get(self, request):
        watchlists = Watchlist.objects.prefetch_related("items__ticker")
        serializer = WatchlistSerializer(watchlists, many=True)
        return Response(serializer.data)


class WatchlistRefreshView(APIView):
    """
    Para cada Ticker del watchlist: load_prices_for_ticker (días=REFRESH_DAYS)
    y después compute_score. Mismo espíritu que el resumen de
    load_prices/compute_scores management commands, pero como response
    JSON en vez de stdout -- un ticker roto no tumba el resto.
    """

    def post(self, request, pk):
        watchlist = get_object_or_404(Watchlist, pk=pk)
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

        return Response(
            {
                "watchlist": watchlist.name,
                "tickers_procesados": processed,
                "errores_de_precio": price_errors,
                "scores_actualizados": scores_updated,
                "omitidos_por_falta_de_historico": skipped_insufficient_history,
            }
        )


class ScoreDetailView(APIView):
    def get(self, request, symbol):
        symbol = symbol.upper()
        ticker = Ticker.objects.filter(symbol=symbol).first()
        if ticker is None:
            return Response(
                {"detail": f"ticker '{symbol}' no encontrado"},
                status=status.HTTP_404_NOT_FOUND,
            )

        latest_score = ticker.scores.order_by("-date").first()
        if latest_score is None:
            return Response(
                {
                    "detail": (
                        f"'{symbol}' no tiene ningún score calculado todavía "
                        "-- correr compute_scores primero"
                    )
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = ScoreSerializer(latest_score)
        return Response(serializer.data)


class ScoreExplainView(APIView):
    throttle_classes = [AgentExplainThrottle]

    def get(self, request, symbol):
        symbol = symbol.upper()

        try:
            explanation, error = explain_and_persist(symbol)
        except AgentError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

        if explanation is None:
            return Response({"detail": error}, status=status.HTTP_404_NOT_FOUND)

        serializer = AgentExplanationSerializer(explanation)
        return Response(serializer.data)
