"""
run_backtest(ticker, horizon_days, bucket_edges): backtest "ingenuo" del
Score técnico contra retornos futuros reales -- para cada fecha d con
suficiente histórico HASTA d (nunca usa PriceBar posteriores a d: cero
look-ahead, la pieza más importante de este módulo), calcula el score
con los datos que existían en ese momento (_compute_score_from_bars,
reusada tal cual de scoring/services.py -- no se duplica la matemática
del score acá) y lo compara contra el retorno real horizon_days sesiones
de trading después.

Limitaciones (documentadas también en CLAUDE.md -- no se esconden):
- Un solo ticker por corrida, no una cartera ni un universo.
- Sin costos de transacción ni slippage.
- No es point-in-time real: usa el Ticker tal como existe HOY (símbolo,
  activo); no reconstruye qué tickers formaban un índice o watchlist en
  el pasado.
- Muestra chica y variable según cuánta historia real tenga cada ticker.
"""

import logging

from scoring.services import MIN_BARS_REQUIRED, _compute_score_from_bars

logger = logging.getLogger(__name__)

DEFAULT_HORIZON_DAYS = 10
DEFAULT_BUCKET_EDGES = (40, 60, 80)


def _all_bucket_labels(bucket_edges):
    labels = []
    lower = 0
    for edge in bucket_edges:
        labels.append(f"{lower}-{edge}")
        lower = edge
    labels.append(f"{lower}-100")
    return labels


def _bucket_label(score, bucket_edges):
    lower = 0
    for edge in bucket_edges:
        if score < edge:
            return f"{lower}-{edge}"
        lower = edge
    return f"{lower}-100"


def run_backtest(ticker, horizon_days=DEFAULT_HORIZON_DAYS, bucket_edges=DEFAULT_BUCKET_EDGES):
    """
    Devuelve:
    - {"ticker", "horizon_days", "total_points", "points", "buckets"} si
      hay al menos un punto válido, donde:
        * "points": [{"date", "score", "forward_return_pct"}, ...] en
          orden cronológico -- traza completa, punto por punto.
        * "buckets": {label: {"n", "avg_return_pct", "win_rate_pct"}, ...}
          en orden ascendente de score, solo buckets con >=1 punto.
    - {"ticker", "reason"} si no hay histórico suficiente para ningún
      punto -- nunca lanza excepción.
    """
    bars = list(ticker.price_bars.order_by("date"))

    points = []
    for i in range(MIN_BARS_REQUIRED - 1, len(bars)):
        future_index = i + horizon_days
        if future_index >= len(bars):
            continue  # no hay suficientes bars futuros -- se descarta el punto

        # bars[: i + 1] -- hasta el índice i (la fecha d) inclusive,
        # NUNCA bars posteriores. Esto es lo que evita el look-ahead.
        score, _ = _compute_score_from_bars(bars[: i + 1])

        close_d = float(bars[i].close)
        close_future = float(bars[future_index].close)
        forward_return_pct = (close_future / close_d - 1) * 100

        points.append(
            {
                "date": bars[i].date.isoformat(),
                "score": score,
                "forward_return_pct": forward_return_pct,
            }
        )

    if not points:
        reason = (
            f"'{ticker.symbol}' no tiene suficiente histórico para ningún "
            f"punto de backtest (hacen falta al menos {MIN_BARS_REQUIRED} "
            f"barras hasta una fecha, más {horizon_days} sesiones de "
            "trading posteriores)."
        )
        logger.info("run_backtest: omitido -- %s", reason)
        return {"ticker": ticker.symbol, "reason": reason}

    grouped = {}
    for point in points:
        label = _bucket_label(point["score"], bucket_edges)
        grouped.setdefault(label, []).append(point["forward_return_pct"])

    buckets = {}
    for label in _all_bucket_labels(bucket_edges):
        returns = grouped.get(label)
        if not returns:
            continue
        n = len(returns)
        buckets[label] = {
            "n": n,
            "avg_return_pct": sum(returns) / n,
            "win_rate_pct": sum(1 for r in returns if r > 0) / n * 100,
        }

    return {
        "ticker": ticker.symbol,
        "horizon_days": horizon_days,
        "total_points": len(points),
        "points": points,
        "buckets": buckets,
    }
