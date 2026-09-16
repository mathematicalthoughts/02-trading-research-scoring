"""
compute_score(ticker): calcula el Score técnico (0-100) de un ticker a
partir de su histórico de PriceBar, usando las funciones puras de
indicators.py (sin pandas-ta -- ver CLAUDE.md).

Ponderación del score, documentada acá y en CLAUDE.md para poder
defenderla en entrevista:

- Tendencia (40 pts, dos condiciones independientes de 20 pts cada una):
    * close > sma50 -- el precio está por encima de la media larga.
    * sma20 > sma50 -- la media corta está por encima de la larga
      (cruce alcista).
  Se reparten 0, 20 o 40 pts según cuántas se cumplan.

- Momentum (30 pts, RSI14 mapeado a un puntaje que penaliza los
  extremos en vez de premiarlos -- un RSI > 70 o < 30 es una señal de
  riesgo de reversión, no de fortaleza):
    * 40-60 (zona neutral): 15 pts.
    * [30, 40) o (60, 70] (momentum sano, sin extremo): 30 pts.
    * < 30 o > 70 (sobrecompra/sobreventa): 5 pts.

- Volumen (30 pts, relative_volume = volumen de hoy / promedio de los
  20 días previos, como confirmación de la señal de precio):
    * >= 1.5: 30 pts (confirmación fuerte).
    * [1.0, 1.5): 15 pts.
    * < 1.0: 0 pts.

ATR14 se calcula y se guarda en `components` como referencia de
volatilidad (útil para backtesting/stop-loss más adelante) pero NO suma
ni resta puntos del score: mide cuánto se mueve el precio, no hacia
dónde -- no es direccional.
"""

import logging

from .indicators import atr, relative_volume, rsi, sma

logger = logging.getLogger(__name__)

# SMA50 es el indicador que más histórico pide de los cuatro (RSI14 y
# ATR14 necesitan 15 barras, relative_volume 21) -- 50 barras alcanzan
# para los cuatro con margen, así que ningún indicador puede devolver
# None una vez pasado este check.
MIN_BARS_REQUIRED = 50


def _trend_points(close, sma20, sma50):
    points = 0
    if close > sma50:
        points += 20
    if sma20 > sma50:
        points += 20
    return points


def _momentum_points(rsi14):
    if 40 <= rsi14 <= 60:
        return 15
    if 30 <= rsi14 < 40 or 60 < rsi14 <= 70:
        return 30
    return 5


def _volume_points(rel_volume):
    if rel_volume >= 1.5:
        return 30
    if rel_volume >= 1.0:
        return 15
    return 0


def compute_score(ticker):
    """
    Devuelve (score:int, components:dict) si hay suficiente histórico, o
    (None, razon:str) si `ticker` tiene menos de MIN_BARS_REQUIRED
    PriceBar -- nunca lanza excepción por falta de datos.
    """
    bars = list(ticker.price_bars.order_by("date"))

    if len(bars) < MIN_BARS_REQUIRED:
        reason = (
            f"'{ticker.symbol}' tiene {len(bars)} barra(s), se necesitan "
            f"al menos {MIN_BARS_REQUIRED} para calcular SMA50."
        )
        logger.info("compute_score: omitido -- %s", reason)
        return None, reason

    closes = [float(bar.close) for bar in bars]
    volumes = [float(bar.volume) for bar in bars]

    sma20 = sma(closes, 20)
    sma50 = sma(closes, 50)
    rsi14 = rsi(closes, 14)
    atr14 = atr(bars, 14)
    rel_volume = relative_volume(volumes, 20)

    trend_points = _trend_points(closes[-1], sma20, sma50)
    momentum_points = _momentum_points(rsi14)
    volume_points = _volume_points(rel_volume)

    score = trend_points + momentum_points + volume_points

    components = {
        "sma20": sma20,
        "sma50": sma50,
        "rsi14": rsi14,
        "relative_volume": rel_volume,
        "atr14": atr14,
        "trend_points": trend_points,
        "momentum_points": momentum_points,
        "volume_points": volume_points,
    }

    return score, components
