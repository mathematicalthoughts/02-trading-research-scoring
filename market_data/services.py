import yfinance as yf

from .models import PriceBar, Ticker


class PriceLoadError(Exception):
    """Error al descargar o guardar el histórico OHLCV de un ticker."""


def load_prices_for_ticker(ticker: Ticker, days: int) -> int:
    """
    Descarga el histórico diario OHLCV de `ticker` para los últimos `days`
    días vía yfinance y hace update_or_create en PriceBar por (ticker, date)
    -- correr esto dos veces con los mismos datos no duplica filas.

    Devuelve la cantidad de filas creadas/actualizadas. Propaga cualquier
    excepción (símbolo inválido, error de red, respuesta vacía) como
    PriceLoadError -- quien llama (el management command) decide cómo
    loguearla y seguir con el resto de los tickers.
    """
    try:
        history = yf.Ticker(ticker.symbol).history(period=f"{days}d")
    except Exception as exc:
        raise PriceLoadError(
            f"Fallo al descargar histórico de '{ticker.symbol}': {exc}"
        ) from exc

    if history is None or history.empty:
        raise PriceLoadError(
            f"yfinance no devolvió datos para '{ticker.symbol}' "
            f"(símbolo inválido o sin histórico en el período pedido)."
        )

    rows = 0
    for index, row in history.iterrows():
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

    return rows
