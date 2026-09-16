"""
Indicadores técnicos calculados a mano (sin pandas-ta -- ver CLAUDE.md:
pandas-ta importa `from numpy import NaN`, eliminado en numpy>=2.0, y
además el roadmap de este portafolio pide poder explicar cada indicador
con criterio propio en la entrevista, no citando una librería como caja
negra).

Todas las funciones son puras: reciben datos ya ordenados por fecha
ascendente (el más viejo primero, el más nuevo al final), nunca tocan la
base de datos, y devuelven None -- nunca lanzan excepción -- cuando no
hay suficiente histórico para calcular el indicador.
"""


def sma(closes, window):
    """Media móvil simple de los últimos `window` cierres."""
    closes = list(closes)
    if len(closes) < window:
        return None
    return sum(float(c) for c in closes[-window:]) / window


def rsi(closes, period=14):
    """
    RSI de Wilder. Necesita al menos period+1 cierres (period diffs).

    El primer promedio de ganancias/pérdidas es un promedio simple de los
    primeros `period` diffs; de ahí en adelante se suaviza con el método
    de Wilder: avg = (avg_prev * (period - 1) + valor_nuevo) / period.
    """
    closes = [float(c) for c in closes]
    if len(closes) < period + 1:
        return None

    diffs = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0.0 for d in diffs]
    losses = [-d if d < 0 else 0.0 for d in diffs]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for gain, loss in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def atr(bars, period=14):
    """
    Average True Range de Wilder. `bars` son objetos con atributos .high,
    .low, .close (ej. instancias de PriceBar), ordenados por fecha
    ascendente. Necesita al menos period+1 barras -- cada true range usa
    el close de la barra anterior, así que period true ranges requieren
    period+1 barras.
    """
    bars = list(bars)
    if len(bars) < period + 1:
        return None

    true_ranges = []
    for i in range(1, len(bars)):
        high = float(bars[i].high)
        low = float(bars[i].low)
        prev_close = float(bars[i - 1].close)
        true_ranges.append(
            max(high - low, abs(high - prev_close), abs(low - prev_close))
        )

    atr_value = sum(true_ranges[:period]) / period
    for true_range in true_ranges[period:]:
        atr_value = (atr_value * (period - 1) + true_range) / period

    return atr_value


def relative_volume(volumes, window=20):
    """
    Volumen del último día sobre el promedio de los `window` días previos
    (sin incluir el último día). Necesita al menos window+1 valores.
    """
    volumes = [float(v) for v in volumes]
    if len(volumes) < window + 1:
        return None

    previous = volumes[-(window + 1):-1]
    avg_previous = sum(previous) / window
    if avg_previous == 0:
        return None

    return volumes[-1] / avg_previous
