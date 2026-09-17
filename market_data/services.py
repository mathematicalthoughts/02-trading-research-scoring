import logging
import math
from datetime import timedelta

import yfinance as yf

from .models import PriceBar, Ticker

logger = logging.getLogger(__name__)

# Margen para capturar revisiones tardías de datos de yfinance en los
# últimos días (ej. un cierre ajustado horas después) sin tener que
# volver a bajar todo el histórico -- ver load_prices_for_ticker.
OVERLAP_DAYS = 5


class PriceLoadError(Exception):
    """Error al descargar o guardar el histórico OHLCV de un ticker."""


class TickerMetadataError(Exception):
    """El symbol no resuelve a un instrumento válido en yfinance."""


def load_prices_for_ticker(ticker: Ticker, days: int) -> int:
    """
    Descarga el histórico diario OHLCV de `ticker` vía yfinance y hace
    update_or_create en PriceBar por (ticker, date) -- correr esto dos
    veces con los mismos datos no duplica filas.

    El fetch de `days` días completos es SOLO el bootstrap único de un
    ticker que todavía no tiene ningún PriceBar. Si el ticker ya tiene
    historial, el refresh es INCREMENTAL: se pide a yfinance desde
    (último date guardado - OVERLAP_DAYS), nunca los `days` completos de
    nuevo. Bug real visto en producción (Render, logs con "WORKER
    TIMEOUT" + SIGKILL por presunto out-of-memory -- no una excepción de
    Python): con el fetch completo en CADA refresh, una watchlist con
    varios tickers de historia profunda (REFRESH_DAYS=500 c/u,
    secuencial dentro del mismo request) no entraba en el timeout del
    worker de Gunicorn del plan free/starter de Render.

    Limitación conocida, no resuelta acá: si se agregan varios tickers
    NUEVOS al mismo watchlist y se aprieta "Refrescar precios" una sola
    vez, todavía se hace el bootstrap completo de cada uno en el mismo
    request -- agregar tickers de a uno y refrescar es el flujo normal
    que evita esto.

    Devuelve la cantidad de filas creadas/actualizadas. Una fila con
    Open/High/Low/Close o Volume = NaN (común en la barra del día en
    curso, o por dividendos/splits) se saltea -- no se guarda, no rompe
    el resto del histórico, y queda logueada (nunca silenciada del todo).

    Todo el fetch + la escritura viven bajo el mismo try/except: antes
    solo el fetch estaba cubierto, y una fila con NaN hacía explotar
    `int(row["Volume"])` sin capturar (bug real: reventaba como 500 en
    DashboardView.post, que solo atrapa PriceLoadError). Propaga
    cualquier excepción real (símbolo inválido, error de red, respuesta
    vacía, o cualquier error inesperado durante la escritura) como
    PriceLoadError -- quien llama decide cómo loguearla y seguir con el
    resto de los tickers.
    """
    try:
        latest_bar = ticker.price_bars.order_by("-date").first()
        if latest_bar is not None:
            start = latest_bar.date - timedelta(days=OVERLAP_DAYS)
            history = yf.Ticker(ticker.symbol).history(start=start)
        else:
            history = yf.Ticker(ticker.symbol).history(period=f"{days}d")

        if history is None or history.empty:
            raise PriceLoadError(
                f"yfinance no devolvió datos para '{ticker.symbol}' "
                f"(símbolo inválido o sin histórico en el período pedido)."
            )

        rows = 0
        skipped = 0

        for index, row in history.iterrows():
            values = (row["Open"], row["High"], row["Low"], row["Close"], row["Volume"])
            if any(math.isnan(value) for value in values):
                skipped += 1
                continue

            PriceBar.objects.update_or_create(
                ticker=ticker,
                date=index.date(),
                defaults={
                    "open": row["Open"],
                    "high": row["High"],
                    "low": row["Low"],
                    "close": row["Close"],
                    "volume": int(row["Volume"]),
                },
            )
            rows += 1

        if skipped:
            logger.warning(
                "load_prices_for_ticker: %s fila(s) salteada(s) por NaN para '%s'",
                skipped,
                ticker.symbol,
            )

        return rows
    except PriceLoadError:
        raise
    except Exception as exc:
        raise PriceLoadError(
            f"Fallo al descargar histórico de '{ticker.symbol}': {exc}"
        ) from exc


def fetch_ticker_metadata(symbol: str) -> dict:
    """
    Resuelve name/exchange de un symbol vía yfinance.get_info(). Si no hay
    longName/shortName (símbolo inválido, o yfinance no devuelve datos, o
    la llamada falla), levanta TickerMetadataError -- el símbolo no existe
    o no es válido, y no debe crearse ningún Ticker/WatchlistItem a partir
    de eso.

    Usada solo por dashboard/views.py::WatchlistTickerAddView -- ver
    CLAUDE.md, sección "Frontend", para por qué esta es la única excepción
    a "nunca llamar servicios externos desde una vista sync del dashboard".
    """
    try:
        info = yf.Ticker(symbol).get_info()
    except Exception as exc:
        raise TickerMetadataError(f"'{symbol}' no parece un ticker válido: {exc}") from exc

    name = info.get("longName") or info.get("shortName")
    if not name:
        raise TickerMetadataError(f"'{symbol}' no parece un ticker válido.")

    return {"name": name, "exchange": info.get("exchange", "")}
