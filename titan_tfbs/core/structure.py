"""Market structure primitives.

TFBS Ch II, Pillar 1: "Structure First: We trade formations, not indicators.
Price structure is the primary signal."  Everything the pattern modules need
is built here — swing pivots, trend classification, support/resistance levels,
trendline (neckline) geometry, and the rejection candles that trigger Method B
and Method C entries.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import List, Optional, Sequence

from titan_tfbs.core.candles import Candle
from titan_tfbs.core.indicators import atr, ema


class PivotType(str, Enum):
    HIGH = "high"
    LOW = "low"


class Trend(str, Enum):
    """TFBS Ch IX Screen 1 output."""

    UP = "up"
    DOWN = "down"
    RANGE = "range"

    @property
    def sign(self) -> int:
        return {Trend.UP: 1, Trend.DOWN: -1, Trend.RANGE: 0}[self]


@dataclass(frozen=True)
class Pivot:
    """A confirmed swing point."""

    index: int
    ts: datetime
    price: float
    kind: PivotType
    #: Average volume across the leg leading into this pivot — used by the
    #: Ch III-C and Ch IV-C volume-profile filters.
    leg_volume: float = 0.0

    @property
    def is_high(self) -> bool:
        return self.kind is PivotType.HIGH


def find_pivots(
    candles: Sequence[Candle],
    lookback: int = 3,
    min_swing: float = 0.0,
) -> List[Pivot]:
    """Fractal pivot detection.

    A pivot high at ``i`` requires ``lookback`` bars on each side with lower
    highs.  The trailing ``lookback`` bars can never host a confirmed pivot,
    which is exactly right for a live engine: a swing is not a swing until the
    market has moved away from it.

    ``min_swing`` (in price units) suppresses noise pivots whose excursion from
    the previous opposite pivot is too small to be structural.
    """
    n = len(candles)
    pivots: List[Pivot] = []
    if n < 2 * lookback + 1:
        return pivots

    for i in range(lookback, n - lookback):
        window = candles[i - lookback : i + lookback + 1]
        c = candles[i]
        highs = [w.high for w in window]
        lows = [w.low for w in window]
        if c.high >= max(highs) and _strictly_dominant(highs, lookback, high=True):
            pivots.append(Pivot(i, c.ts, c.high, PivotType.HIGH))
        elif c.low <= min(lows) and _strictly_dominant(lows, lookback, high=False):
            pivots.append(Pivot(i, c.ts, c.low, PivotType.LOW))

    pivots = _alternate(pivots)
    if min_swing > 0:
        pivots = _filter_small_swings(pivots, min_swing)
        pivots = _alternate(pivots)
    return _attach_leg_volume(pivots, candles)


def _strictly_dominant(values: Sequence[float], lookback: int, high: bool) -> bool:
    """Guard against flat plateaus registering as pivots on both sides."""
    centre = values[lookback]
    left = values[:lookback]
    right = values[lookback + 1 :]
    if high:
        return all(v < centre for v in left) and all(v <= centre for v in right)
    return all(v > centre for v in left) and all(v >= centre for v in right)


def _alternate(pivots: List[Pivot]) -> List[Pivot]:
    """Collapse consecutive same-type pivots, keeping the most extreme."""
    out: List[Pivot] = []
    for p in pivots:
        if out and out[-1].kind is p.kind:
            better = (p.price > out[-1].price) if p.is_high else (p.price < out[-1].price)
            if better:
                out[-1] = p
            continue
        out.append(p)
    return out


def _filter_small_swings(pivots: List[Pivot], min_swing: float) -> List[Pivot]:
    if len(pivots) < 3:
        return pivots
    out = [pivots[0]]
    for p in pivots[1:]:
        if abs(p.price - out[-1].price) < min_swing:
            # Too shallow to be structural; keep whichever is more extreme.
            if out[-1].kind is p.kind:
                better = (
                    (p.price > out[-1].price) if p.is_high else (p.price < out[-1].price)
                )
                if better:
                    out[-1] = p
            continue
        out.append(p)
    return out


def _attach_leg_volume(pivots: List[Pivot], candles: Sequence[Candle]) -> List[Pivot]:
    """Average volume of the leg that produced each pivot.

    This is what the Ch III-C and Ch IV-C volume-profile filters compare.  The
    first pivot has no predecessor, so its leg is bounded to the length of the
    following one rather than running back to the start of history — otherwise
    the left shoulder's reading would be diluted by every bar before it.
    """
    out: List[Pivot] = []
    for i, p in enumerate(pivots):
        if i > 0:
            start = pivots[i - 1].index
        elif len(pivots) > 1:
            start = max(0, p.index - (pivots[1].index - p.index))
        else:
            start = max(0, p.index - 10)
        leg = candles[start : p.index + 1]
        vol = sum(c.volume for c in leg) / len(leg) if leg else 0.0
        out.append(Pivot(p.index, p.ts, p.price, p.kind, vol))
    return out


# --------------------------------------------------------------------------
# Trend
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class TrendState:
    """Structural read of a timeframe, used for the Ch IX bias screens."""

    trend: Trend
    #: 0.0-1.0 — how cleanly the structure and EMAs agree.
    strength: float
    swing_bias: Trend
    ema_bias: Trend
    last_pivots: List[Pivot]

    @property
    def is_directional(self) -> bool:
        return self.trend is not Trend.RANGE


def classify_trend(
    candles: Sequence[Candle],
    pivots: Optional[List[Pivot]] = None,
    fast_ema: int = 20,
    slow_ema: int = 50,
    swing_lookback: int = 3,
) -> TrendState:
    """Classify a timeframe as UP / DOWN / RANGE.

    Two independent reads are combined, as Ch IX asks for a structural bias
    supported (not driven) by moving averages:

    * swing structure — higher highs & higher lows vs lower highs & lower lows
    * EMA relationship — fast vs slow, and price vs fast
    """
    if pivots is None:
        pivots = find_pivots(candles, lookback=swing_lookback)

    # A "higher high" has to be meaningfully higher. Without a tolerance the
    # equal highs of a range register as a trend on nothing but noise.
    atr_value = atr(candles, 14) or 0.0
    swing_bias = _swing_bias(pivots, tolerance=0.25 * atr_value)
    ema_bias = _ema_bias(candles, fast_ema, slow_ema)

    if swing_bias is Trend.RANGE:
        # Ch II, Pillar 1: "We trade formations, not indicators." Moving
        # averages are supplementary and may not declare a trend on their own.
        trend, strength = Trend.RANGE, 0.0
    elif swing_bias is ema_bias:
        trend, strength = swing_bias, 1.0
    elif ema_bias is Trend.RANGE:
        trend, strength = swing_bias, 0.65
    else:
        trend, strength = Trend.RANGE, 0.0   # structure and EMAs disagree

    return TrendState(trend, strength, swing_bias, ema_bias, pivots[-6:])


def _swing_bias(pivots: Sequence[Pivot], tolerance: float = 0.0) -> Trend:
    highs = [p for p in pivots if p.is_high][-3:]
    lows = [p for p in pivots if not p.is_high][-3:]
    if len(highs) < 2 or len(lows) < 2:
        return Trend.RANGE
    hh = highs[-1].price > highs[-2].price + tolerance
    hl = lows[-1].price > lows[-2].price + tolerance
    lh = highs[-1].price < highs[-2].price - tolerance
    ll = lows[-1].price < lows[-2].price - tolerance
    if hh and hl:
        return Trend.UP
    if lh and ll:
        return Trend.DOWN
    return Trend.RANGE


def _ema_bias(candles: Sequence[Candle], fast: int, slow: int) -> Trend:
    closes = [c.close for c in candles]
    f, s = ema(closes, fast), ema(closes, slow)
    if f is None or s is None:
        return Trend.RANGE
    price = closes[-1]
    if f > s and price > f:
        return Trend.UP
    if f < s and price < f:
        return Trend.DOWN
    return Trend.RANGE


def prior_trend_leg(
    candles: Sequence[Candle],
    end_index: int,
    bars: int,
    direction: Trend,
) -> Optional[float]:
    """Measure the impulse leg preceding a formation.

    TFBS Ch III-E and Ch IV-C both make a "clear established trend preceding
    the formation (min 20 bars)" a MANDATORY filter — these are reversal
    patterns, and without a trend there is nothing to reverse.

    Returns the size of the leg in price units, or None if the required
    direction is not present.
    """
    start = end_index - bars
    if start < 0 or end_index >= len(candles):
        return None
    window = candles[start : end_index + 1]
    if len(window) < 3:
        return None
    if direction is Trend.UP:
        low = min(c.low for c in window)
        high = window[-1].high
        # The leg must actually rise into the formation.
        if high <= low or window[-1].close <= window[0].close:
            return None
        return high - low
    if direction is Trend.DOWN:
        high = max(c.high for c in window)
        low = window[-1].low
        if low >= high or window[-1].close >= window[0].close:
            return None
        return high - low
    return None


def prior_trend_is_established(
    candles: Sequence[Candle],
    end_index: int,
    bars: int,
    direction: Trend,
    swing_lookback: int = 3,
    fast_ema: int = 20,
    slow_ema: int = 50,
) -> bool:
    """Is the move into a formation a *trend*, or just the last leg of a range?

    TFBS Ch III-E and Ch IV-C both call for a "clear established trend"
    preceding the formation, and Ch IV-C adds that a pattern inside a wider
    range "= noise".  Leg size alone cannot tell those apart — one swing of an
    oscillation is as large as one leg of a trend — so the structure of the
    window leading into the formation is classified as well.
    """
    if direction is Trend.RANGE:
        return False
    # The window has to be long enough to hold several swings and to seed the
    # slow EMA, otherwise a genuine trend reads as "no structure".
    span = max(bars * 5, slow_ema + 20)
    start = max(0, end_index - span)
    window = candles[start : end_index + 1]
    if len(window) < bars:
        return False
    state = classify_trend(
        window, fast_ema=fast_ema, slow_ema=slow_ema, swing_lookback=swing_lookback
    )
    return state.trend is direction


# --------------------------------------------------------------------------
# Lines (necklines / confirmation lines)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Line:
    """A straight line in (bar index, price) space — the neckline model."""

    x1: int
    y1: float
    x2: int
    y2: float

    @property
    def slope(self) -> float:
        """Price change per bar."""
        return 0.0 if self.x2 == self.x1 else (self.y2 - self.y1) / (self.x2 - self.x1)

    def value_at(self, x: int) -> float:
        return self.y1 + self.slope * (x - self.x1)

    def angle_deg(self, atr_value: float) -> float:
        """Slope as an angle, normalised by ATR so it is scale free.

        TFBS Ch III-E requires a "flat or gently sloping (< 15 degrees)"
        neckline.  Degrees are meaningless on a raw price chart (they depend on
        the pixel aspect ratio), so the firm rule is implemented as: one ATR of
        price movement per bar equals 45 degrees.
        """
        if atr_value <= 0:
            return 0.0
        return math.degrees(math.atan(self.slope / atr_value))


def horizontal_line(x1: int, x2: int, price: float) -> Line:
    return Line(x1, price, x2, price)


# --------------------------------------------------------------------------
# Support / resistance
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Level:
    """A clustered support/resistance level."""

    price: float
    touches: int
    kind: PivotType
    last_index: int

    @property
    def is_resistance(self) -> bool:
        return self.kind is PivotType.HIGH


def find_levels(
    candles: Sequence[Candle],
    pivots: Optional[List[Pivot]] = None,
    tolerance_atr: float = 0.5,
    atr_period: int = 14,
    min_touches: int = 2,
    swing_lookback: int = 3,
) -> List[Level]:
    """Cluster swing pivots into S/R levels.

    Feeds the Ch XI "S/R Confluence" and "Clean Path" confluence factors and
    the Ch X-B TP2 ("next significant S/R beyond TP1") target.
    """
    if pivots is None:
        pivots = find_pivots(candles, lookback=swing_lookback)
    if not pivots:
        return []
    a = atr(candles, atr_period) or 0.0
    tol = a * tolerance_atr
    if tol <= 0:
        tol = max(1e-9, (max(c.high for c in candles) - min(c.low for c in candles)) * 0.002)

    levels: List[Level] = []
    for kind in (PivotType.HIGH, PivotType.LOW):
        group = sorted([p for p in pivots if p.kind is kind], key=lambda p: p.price)
        cluster: List[Pivot] = []
        for p in group:
            if cluster and abs(p.price - cluster[-1].price) > tol:
                levels.append(_to_level(cluster, kind))
                cluster = []
            cluster.append(p)
        if cluster:
            levels.append(_to_level(cluster, kind))

    return sorted(
        [lv for lv in levels if lv.touches >= min_touches or lv.touches == 1],
        key=lambda lv: lv.price,
    )


def _to_level(cluster: Sequence[Pivot], kind: PivotType) -> Level:
    price = sum(p.price for p in cluster) / len(cluster)
    return Level(price, len(cluster), kind, max(p.index for p in cluster))


def nearest_level_beyond(
    levels: Sequence[Level], price: float, direction: int, min_distance: float = 0.0
) -> Optional[Level]:
    """The first S/R level further along ``direction`` (+1 up, -1 down)."""
    candidates = [
        lv
        for lv in levels
        if (lv.price - price) * direction >= min_distance
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda lv: abs(lv.price - price))


def levels_between(
    levels: Sequence[Level], a: float, b: float, exclude_within: float = 0.0
) -> List[Level]:
    """Levels strictly between two prices — the Ch XI "clean path" test."""
    lo, hi = (a, b) if a < b else (b, a)
    return [
        lv
        for lv in levels
        if lo + exclude_within < lv.price < hi - exclude_within
    ]


# --------------------------------------------------------------------------
# Rejection candles (Ch VII Method B / C triggers)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RejectionSignal:
    kind: str            # "pin_bar" | "engulfing" | "strong_wick"
    bullish: bool
    strength: float      # 0.0-1.0
    candle: Candle


def detect_rejection(
    candles: Sequence[Candle],
    bullish: bool,
    pin_wick_ratio: float = 2.0,
    pin_max_body_frac: float = 0.35,
    engulf_min_body_ratio: float = 1.0,
) -> Optional[RejectionSignal]:
    """Detect the rejection signal Ch VII Method B requires at the retest.

    The manual names three acceptable forms: "pin bar, engulfing, strong wick
    rejection".  All three are implemented; the strongest match wins.
    """
    if not candles:
        return None
    c = candles[-1]
    if c.range <= 0:
        return None

    rejection_wick = c.lower_wick if bullish else c.upper_wick
    opposite_wick = c.upper_wick if bullish else c.lower_wick
    body = c.body

    best: Optional[RejectionSignal] = None

    # Pin bar: long rejection wick, small body, closing back in our direction.
    if (
        rejection_wick >= pin_wick_ratio * max(body, 1e-12)
        and c.body_frac() <= pin_max_body_frac
        and rejection_wick > opposite_wick
    ):
        strength = min(1.0, rejection_wick / c.range)
        best = RejectionSignal("pin_bar", bullish, strength, c)

    # Engulfing: body engulfs the prior body and closes in our direction.
    if len(candles) >= 2:
        p = candles[-2]
        directional = c.is_bullish if bullish else c.is_bearish
        opposite_prior = p.is_bearish if bullish else p.is_bullish
        engulfs = (
            directional
            and opposite_prior
            and body >= engulf_min_body_ratio * max(p.body, 1e-12)
            and (c.close > p.open if bullish else c.close < p.open)
            and (c.open <= p.close if bullish else c.open >= p.close)
        )
        if engulfs:
            strength = min(1.0, body / max(p.body, 1e-12) / 2.0)
            if best is None or strength > best.strength:
                best = RejectionSignal("engulfing", bullish, strength, c)

    # Strong wick: rejection wick dominates the bar even without a pin body.
    if best is None and rejection_wick / c.range >= 0.5 and rejection_wick > opposite_wick:
        directional = c.close > c.open if bullish else c.close < c.open
        if directional or c.body_frac() < 0.5:
            best = RejectionSignal(
                "strong_wick", bullish, rejection_wick / c.range, c
            )

    return best


def is_followthrough(prior: Candle, candle: Candle, bullish: bool) -> bool:
    """TFBS Ch VII Method C — the confirming follow-through candle.

    Requires a close beyond the rejection candle's extreme in the trade
    direction: maximum confirmation, less reward.
    """
    if bullish:
        return candle.is_bullish and candle.close > prior.high
    return candle.is_bearish and candle.close < prior.low


def swing_stop_price(
    candles: Sequence[Candle], bullish: bool, lookback: int, buffer: float
) -> Optional[float]:
    """Most recent swing extreme plus a buffer — the Ch X-A trailing option."""
    if not candles:
        return None
    window = candles[-lookback:] if lookback > 0 else list(candles)
    if not window:
        return None
    if bullish:
        return min(c.low for c in window) - buffer
    return max(c.high for c in window) + buffer
