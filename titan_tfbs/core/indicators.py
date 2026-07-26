"""Indicators.

TFBS Ch II is explicit: "Not an indicator-based system. MAs, RSI, MACD are
supplementary — never primary signals."  Accordingly nothing here generates a
signal.  These are measurement tools used for:

* ATR — normalising thresholds so the same config works on gold and EURUSD
* Volume SMA — the Ch V-A "1.5x the 20-period average" breakout criterion
* RSI / MACD — the Ch V-B divergence *skepticism* filter only
* EMA — Ch IX bias support and the Ch X-A 20-EMA trailing option
"""

from __future__ import annotations

from typing import List, Optional, Sequence

from titan_tfbs.core.candles import Candle


def sma(values: Sequence[float], period: int) -> Optional[float]:
    """Simple moving average of the last ``period`` values."""
    if period <= 0 or len(values) < period:
        return None
    return sum(values[-period:]) / period


def sma_series(values: Sequence[float], period: int) -> List[Optional[float]]:
    out: List[Optional[float]] = []
    running = 0.0
    for i, v in enumerate(values):
        running += v
        if i >= period:
            running -= values[i - period]
        out.append(running / period if i >= period - 1 else None)
    return out


def ema(values: Sequence[float], period: int) -> Optional[float]:
    series = ema_series(values, period)
    return series[-1] if series else None


def ema_series(values: Sequence[float], period: int) -> List[Optional[float]]:
    """Exponential MA seeded with the first ``period`` values' SMA."""
    out: List[Optional[float]] = [None] * len(values)
    if period <= 0 or len(values) < period:
        return out
    k = 2.0 / (period + 1.0)
    prev = sum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def true_range(candle: Candle, prev_close: Optional[float]) -> float:
    if prev_close is None:
        return candle.high - candle.low
    return max(
        candle.high - candle.low,
        abs(candle.high - prev_close),
        abs(candle.low - prev_close),
    )


def atr(candles: Sequence[Candle], period: int = 14) -> Optional[float]:
    """Wilder's Average True Range over the last ``period`` bars."""
    if len(candles) < period + 1:
        # Fall back to a simple mean of available true ranges so early bars
        # still produce a usable scale rather than blocking the engine.
        if len(candles) < 2:
            return None
        trs = [
            true_range(candles[i], candles[i - 1].close)
            for i in range(1, len(candles))
        ]
        return sum(trs) / len(trs)
    trs = [true_range(candles[i], candles[i - 1].close) for i in range(1, len(candles))]
    value = sum(trs[:period]) / period
    for tr in trs[period:]:
        value = (value * (period - 1) + tr) / period
    return value


def atr_series(candles: Sequence[Candle], period: int = 14) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(candles)
    if len(candles) < period + 1:
        return out
    trs = [0.0] + [
        true_range(candles[i], candles[i - 1].close) for i in range(1, len(candles))
    ]
    value = sum(trs[1 : period + 1]) / period
    out[period] = value
    for i in range(period + 1, len(candles)):
        value = (value * (period - 1) + trs[i]) / period
        out[i] = value
    return out


def rsi(values: Sequence[float], period: int = 14) -> Optional[float]:
    series = rsi_series(values, period)
    return series[-1] if series else None


def rsi_series(values: Sequence[float], period: int = 14) -> List[Optional[float]]:
    """Wilder's RSI. Used only for the Ch V-B divergence filter."""
    out: List[Optional[float]] = [None] * len(values)
    if len(values) <= period:
        return out
    gains = losses = 0.0
    for i in range(1, period + 1):
        delta = values[i] - values[i - 1]
        gains += max(delta, 0.0)
        losses += max(-delta, 0.0)
    avg_gain, avg_loss = gains / period, losses / period
    out[period] = _rsi_from(avg_gain, avg_loss)
    for i in range(period + 1, len(values)):
        delta = values[i] - values[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(delta, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-delta, 0.0)) / period
        out[i] = _rsi_from(avg_gain, avg_loss)
    return out


def _rsi_from(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def macd(
    values: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> Optional[tuple]:
    """Returns ``(macd_line, signal_line, histogram)`` or None."""
    if len(values) < slow + signal:
        return None
    fast_s = ema_series(values, fast)
    slow_s = ema_series(values, slow)
    line = [
        (f - s) if (f is not None and s is not None) else None
        for f, s in zip(fast_s, slow_s)
    ]
    valid = [v for v in line if v is not None]
    if len(valid) < signal:
        return None
    sig_series = ema_series(valid, signal)
    sig = sig_series[-1]
    if sig is None or line[-1] is None:
        return None
    return line[-1], sig, line[-1] - sig


def average_volume(candles: Sequence[Candle], period: int = 20) -> Optional[float]:
    """TFBS Ch V-A criterion 2 baseline: the 20-period average volume."""
    if len(candles) < period:
        return None
    vols = [c.volume for c in candles[-period:]]
    total = sum(vols)
    return total / period if total > 0 else None


def volume_ratio(candles: Sequence[Candle], period: int = 20) -> Optional[float]:
    """Ratio of the most recent bar's volume to the trailing average.

    Returns None when the feed carries no volume (spot FX often does not), so
    callers can treat the Ch V-A volume criterion as unavailable rather than
    failed.
    """
    if len(candles) < period + 1:
        return None
    avg = average_volume(candles[:-1], period)
    if not avg:
        return None
    return candles[-1].volume / avg


def linreg_slope(values: Sequence[float]) -> float:
    """Least-squares slope per bar."""
    n = len(values)
    if n < 2:
        return 0.0
    mean_x = (n - 1) / 2.0
    mean_y = sum(values) / n
    num = sum((i - mean_x) * (v - mean_y) for i, v in enumerate(values))
    den = sum((i - mean_x) ** 2 for i in range(n))
    return num / den if den else 0.0


def stdev(values: Sequence[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    return (sum((v - mean) ** 2 for v in values) / (n - 1)) ** 0.5
