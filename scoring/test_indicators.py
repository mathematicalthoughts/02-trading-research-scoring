"""
Tests de scoring/indicators.py contra valores calculados a mano (no se
compara contra pandas-ta ni ninguna otra librería).
"""

from collections import namedtuple

from .indicators import atr, relative_volume, rsi, sma

Bar = namedtuple("Bar", ["high", "low", "close"])


def test_sma_returns_none_with_not_enough_data():
    assert sma([1, 2, 3], window=5) is None


def test_sma_computes_mean_of_last_window():
    # Solo importan los últimos 3: (3+4+5)/3 = 4
    assert sma([100, 1, 2, 3, 4, 5], window=3) == 4.0


def test_rsi_returns_none_with_not_enough_data():
    # period=14 necesita 15 cierres (14 diffs); acá hay 10.
    assert rsi(list(range(10)), period=14) is None


def test_rsi_matches_hand_calculated_value():
    """
    15 cierres -> 14 diffs: 8 ganancias de +2 (suman 16) y 6 pérdidas de
    -1 (suman 6). Con exactamente 14 diffs no hay suavizado de Wilder
    (el for-loop de smoothing queda vacío), así que:
        avg_gain = 16/14 = 8/7
        avg_loss = 6/14  = 3/7
        RS  = (8/7) / (3/7) = 8/3
        RSI = 100 - 100/(1 + 8/3) = 100 - 300/11 = 800/11 ≈ 72.727272...
    """
    closes = [100, 102, 104, 106, 108, 107, 106, 105, 104, 103, 102, 104, 106, 108, 110]
    assert len(closes) == 15

    expected = 800 / 11
    assert abs(rsi(closes, period=14) - expected) < 1e-9


def test_rsi_all_gains_returns_100():
    # avg_loss == 0 -> caso límite documentado explícitamente.
    closes = list(range(100, 116))  # 16 cierres, todos +1
    assert rsi(closes, period=14) == 100.0


def test_atr_returns_none_with_not_enough_data():
    bars = [Bar(high=11, low=9, close=10) for _ in range(10)]
    assert atr(bars, period=14) is None


def test_atr_matches_hand_calculated_value():
    """
    15 barras planas (close=50, high=51, low=49) -> cada true range de
    los índices 1..14 es max(51-49, |51-50|, |49-50|) = 2. Con
    exactamente 14 true ranges no hay suavizado (loop vacío):
        ATR = promedio de 14 valores de 2 = 2.0
    Se agrega una 16ª barra con un salto grande (high=56, low=50,
    close=54; prev_close=50) -> true range = max(6, 6, 0) = 6, que sí
    dispara un paso de suavizado de Wilder:
        ATR = (2*13 + 6) / 14 = 32/14 = 16/7 ≈ 2.285714...
    """
    flat_bars = [Bar(high=51, low=49, close=50) for _ in range(15)]
    assert atr(flat_bars, period=14) == 2.0

    bars_with_spike = flat_bars + [Bar(high=56, low=50, close=54)]
    expected = 16 / 7
    assert abs(atr(bars_with_spike, period=14) - expected) < 1e-9


def test_relative_volume_returns_none_with_not_enough_data():
    assert relative_volume([1000] * 15, window=20) is None


def test_relative_volume_matches_hand_calculated_value():
    # 20 días previos de 1000, último día de 1500 -> 1500/1000 = 1.5
    volumes = [1000] * 20 + [1500]
    assert relative_volume(volumes, window=20) == 1.5


def test_relative_volume_returns_none_when_previous_average_is_zero():
    volumes = [0] * 20 + [500]
    assert relative_volume(volumes, window=20) is None
