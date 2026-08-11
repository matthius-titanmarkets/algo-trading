"""Deterministic synthetic market data.

Used by the test suite and the ``demo`` command to exercise the engine
end-to-end without a vendor connection.  Paths are built from waypoints and a
seeded LCG so every run is byte-identical — a backtest that changes because
the data changed teaches nothing.

Formations are described as *segment* lists (``bars, target price, volume
multiplier``) relative to a starting price, so they can be chained into a long
continuous series with a real higher-timeframe history behind them.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional, Sequence, Tuple

from titan_tfbs.core.candles import Candle


class _Rng:
    """Small deterministic LCG — no numpy dependency."""

    def __init__(self, seed: int = 12345) -> None:
        self.state = seed & 0xFFFFFFFF

    def next(self) -> float:
        self.state = (1103515245 * self.state + 12345) & 0x7FFFFFFF
        return self.state / 0x7FFFFFFF

    def signed(self) -> float:
        return self.next() * 2.0 - 1.0


#: (bars, target_price, volume_multiplier)
Segment = Tuple[int, float, float]


def generate(
    start_price: float,
    segments: Sequence[Segment],
    start: datetime,
    bar_minutes: int = 5,
    noise: float = 0.25,
    base_volume: float = 1000.0,
    seed: int = 12345,
) -> List[Candle]:
    """Build a candle series that walks through the given price waypoints.

    ``noise`` is expressed as a fraction of the average per-bar leg movement,
    so the wiggle scales with the move rather than the absolute price.
    """
    rng = _Rng(seed)
    candles: List[Candle] = []
    price = start_price
    ts = start if start.tzinfo else start.replace(tzinfo=timezone.utc)
    delta = timedelta(minutes=bar_minutes)

    for bars, target, vol_mult in segments:
        bars = max(1, int(bars))
        step = (target - price) / bars
        scale = max(abs(step), abs(target) * 1e-5) * max(noise, 1e-6)
        for _ in range(bars):
            open_price = price
            drift = step + rng.signed() * scale
            close_price = open_price + drift
            high = max(open_price, close_price) + abs(rng.signed()) * scale * 1.5
            low = min(open_price, close_price) - abs(rng.signed()) * scale * 1.5
            volume = base_volume * vol_mult * (0.75 + 0.5 * rng.next())
            candles.append(Candle(ts, open_price, high, low, close_price, volume))
            price = close_price
            ts += delta
    return candles


# --------------------------------------------------------------------------
# Formation blueprints — segments only, so they can be chained
# --------------------------------------------------------------------------


def head_shoulders_segments(
    neck: float, height: float, unit: int = 12, inverse: bool = False
) -> Tuple[List[Segment], float, float]:
    """A textbook H&S (or Inverse H&S) with prior trend, break and retest.

    Returns ``(segments, entry_price, exit_price)`` where ``entry_price`` is
    where the series must start.  ``unit`` is base bars per pattern-screen bar,
    so the formation spans roughly 30 pattern-screen bars — comfortably past
    the Ch III-E 20-bar requirement.

    Volume declines left shoulder -> head -> right shoulder and surges on the
    neckline break, satisfying the Ch III-C signature.
    """
    s = -1.0 if inverse else 1.0
    # The series opens well below the neckline and trends into the formation,
    # so the Ch III-E prior-trend filter has a real trend to find.
    start = neck - s * height * 2.2
    approach = neck - s * height * 0.75
    ls = neck + s * height * 0.62
    head = neck + s * height
    rs = neck + s * height * 0.60
    trough = neck + s * height * 0.04
    target = neck - s * height

    segments: List[Segment] = [
        # Prior trend: a clear, established move into the formation (Ch III-E),
        # stepped so the structure prints higher highs and higher lows.
        (10 * unit, neck - s * height * 1.5, 1.0),
        (5 * unit, neck - s * height * 1.75, 0.9),
        (10 * unit, neck - s * height * 0.9, 1.05),
        (5 * unit, neck - s * height * 1.15, 0.9),
        (10 * unit, approach, 1.1),
        (14 * unit, ls, 1.35),                      # left shoulder
        (7 * unit, trough, 0.95),                   # back to the neckline
        (10 * unit, head, 1.15),                    # head — the higher high
        (8 * unit, trough - s * 0.02 * height, 0.85),   # flat neckline
        (10 * unit, rs, 0.70),                      # right shoulder — fails
        (3 * unit, neck, 0.90),
        (2 * unit, neck - s * height * 0.30, 2.4),  # the break, on volume
        (4 * unit, neck - s * height * 0.02, 1.1),  # drift back to the level
        # A short poke through the flipped level then a sharp close back
        # below it — this prints the rejection wick Ch VII Method B keys off.
        (2, neck + s * height * 0.03, 1.6),
        (4, neck - s * height * 0.08, 2.0),
        (10 * unit, target, 1.3),                   # measured move
    ]
    return segments, start, target


def double_top_segments(
    confirmation: float,
    height: float,
    unit: int = 12,
    inverse: bool = False,
    triple: bool = False,
) -> Tuple[List[Segment], float, float]:
    """A Double (or Triple) Top / Bottom with prior trend, break and retest."""
    s = -1.0 if inverse else 1.0
    start = confirmation - s * height * 2.2
    peak = confirmation + s * height
    target = confirmation - s * height

    segments: List[Segment] = [
        # Prior trend — Ch IV-C: "Must follow a clear directional move."
        (10 * unit, confirmation - s * height * 1.6, 1.0),
        (5 * unit, confirmation - s * height * 1.85, 0.9),
        (10 * unit, confirmation - s * height * 1.0, 1.05),
        (5 * unit, confirmation - s * height * 1.25, 0.9),
        (10 * unit, confirmation - s * height * 0.85, 1.1),
        (12 * unit, peak, 1.45),                            # first test
        (8 * unit, confirmation, 0.95),                     # confirmation line
        (11 * unit, peak - s * height * 0.015, 1.05),       # second test
    ]
    if triple:
        segments += [
            (7 * unit, confirmation + s * height * 0.06, 0.85),
            (10 * unit, peak - s * height * 0.03, 0.75),    # third test
        ]
    segments += [
        (6 * unit, confirmation + s * height * 0.05, 0.9),
        (2 * unit, confirmation - s * height * 0.28, 2.5),  # break on volume
        (4 * unit, confirmation - s * height * 0.02, 1.1),  # drift back
        (2, confirmation + s * height * 0.03, 1.6),         # poke the flip
        (4, confirmation - s * height * 0.08, 2.0),         # rejection close
        (10 * unit, target, 1.25),                          # measured move
    ]
    return segments, start, target


def range_segments(
    centre: float, amplitude: float, cycles: int = 4, unit: int = 12
) -> Tuple[List[Segment], float, float]:
    """Choppy sideways price — nothing here should produce a TFBS trade."""
    segments: List[Segment] = []
    for _ in range(cycles):
        segments.append((6 * unit, centre + amplitude, 1.0))
        segments.append((6 * unit, centre - amplitude, 1.0))
    return segments, centre, centre - amplitude


# --------------------------------------------------------------------------
# Candle-producing convenience wrappers
# --------------------------------------------------------------------------


def head_and_shoulders(
    start: datetime,
    base_price: float = 2000.0,
    unit: int = 12,
    height: float = 40.0,
    bar_minutes: int = 5,
    seed: int = 7,
    inverse: bool = False,
) -> List[Candle]:
    segments, entry, _ = head_shoulders_segments(base_price, height, unit, inverse)
    return generate(entry, segments, start, bar_minutes, seed=seed)


def double_top(
    start: datetime,
    base_price: float = 1.2000,
    unit: int = 12,
    height: float = 0.0150,
    bar_minutes: int = 5,
    seed: int = 11,
    inverse: bool = False,
    triple: bool = False,
) -> List[Candle]:
    segments, entry, _ = double_top_segments(base_price, height, unit, inverse, triple)
    return generate(entry, segments, start, bar_minutes, seed=seed)


def ranging_market(
    start: datetime,
    base_price: float = 100.0,
    cycles: int = 8,
    unit: int = 12,
    amplitude: float = 1.0,
    bar_minutes: int = 5,
    seed: int = 3,
) -> List[Candle]:
    segments, entry, _ = range_segments(base_price, amplitude, cycles, unit)
    return generate(entry, segments, start, bar_minutes, seed=seed)


# --------------------------------------------------------------------------
# A full multi-month scenario
# --------------------------------------------------------------------------

#: The formation mix used by :func:`firm_scenario`, alternating direction so
#: the daily bias screen is exercised in both directions.
_SCENARIO_PLAN: List[Tuple[str, dict]] = [
    ("hs", {}),
    ("range", {"cycles": 3}),
    ("dt", {}),
    ("hs", {"inverse": True}),
    ("dt", {"inverse": True}),
    ("range", {"cycles": 2}),
    ("dt", {"triple": True}),
    ("hs", {}),
    ("dt", {"inverse": True, "triple": True}),
    ("hs", {"inverse": True}),
    ("range", {"cycles": 3}),
    ("dt", {}),
]


#: A four-block subset for tests, where processing every block is wasted time.
COMPACT_PLAN: List[Tuple[str, dict]] = [
    ("hs", {}),
    ("range", {"cycles": 2}),
    ("dt", {"inverse": True}),
    ("dt", {"triple": True}),
]


def firm_scenario(
    start: datetime,
    base_price: float,
    height_pct: float = 0.02,
    unit: int = 12,
    bar_minutes: int = 5,
    seed: int = 17,
    repeats: int = 1,
    plan: Optional[Sequence[Tuple[str, dict]]] = None,
) -> List[Candle]:
    """A multi-month series containing every TFBS formation plus dead ranges.

    Long enough for the Ch IX Screen 1 daily bias to be real rather than
    "insufficient history", and mixed enough that the Ch V-B and Ch XI filters
    have something to reject.
    """
    segments: List[Segment] = []
    price = base_price
    height = base_price * height_pct
    first_entry = None

    blocks = plan if plan is not None else _SCENARIO_PLAN
    for _ in range(max(1, repeats)):
        for kind, kwargs in blocks:
            if kind == "hs":
                inverse = bool(kwargs.get("inverse"))
                neck = price + (height * 0.75 if not inverse else -height * 0.75)
                block, entry, exit_price = head_shoulders_segments(
                    neck, height, unit, inverse
                )
            elif kind == "dt":
                inverse = bool(kwargs.get("inverse"))
                confirmation = price + (
                    height * 0.85 if not inverse else -height * 0.85
                )
                block, entry, exit_price = double_top_segments(
                    confirmation, height, unit, inverse, bool(kwargs.get("triple"))
                )
            else:
                block, entry, exit_price = range_segments(
                    price, height * 0.35, int(kwargs.get("cycles", 3)), unit
                )
            if first_entry is None:
                first_entry = entry
            # Bridge from where the last block left off into this one.
            segments.append((4 * unit, entry, 1.0))
            segments.extend(block)
            price = exit_price

    return generate(
        first_entry if first_entry is not None else base_price,
        segments,
        start,
        bar_minutes,
        seed=seed,
    )
