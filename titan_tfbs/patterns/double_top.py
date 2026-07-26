"""Pattern Module B — Double / Triple Top & Bottom (TFBS Ch IV).

    "The Double Top is a two-test reversal. Price reaches significant
     resistance, rejects, pulls back to form an interim low (the confirmation
     line), then rallies again to the same resistance. Failure to break through
     on the second test signals exhaustion."               — TFBS Ch IV-A

Structure detected::

    Peak 1 -> Confirmation Line (pullback low) -> Peak 2      [Double Top]
    Peak 1 -> low -> Peak 2 -> low -> Peak 3                  [Triple Top]

The Double Bottom / Triple Bottom are the exact mirror (Ch IV-B).  Triple
formations carry the Ch IV-D "+1 confluence bonus" because "three failed
attempts carry more conviction".
"""

from __future__ import annotations

from typing import List, Optional, Sequence

from titan_tfbs.config import DoubleTopConfig
from titan_tfbs.core.candles import Candle
from titan_tfbs.core.structure import (
    Pivot,
    PivotType,
    Trend,
    horizontal_line,
    prior_trend_is_established,
    prior_trend_leg,
)
from titan_tfbs.patterns.base import (
    FilterResult,
    FilterStatus,
    Pattern,
    PatternType,
    grade_quality,
)

#: DERIVED — structural floor for a two-test formation.
_ABSOLUTE_MIN_BARS = 6


def detect_double_tops(
    candles: Sequence[Candle],
    pivots: Sequence[Pivot],
    cfg: DoubleTopConfig,
    atr_value: float,
    symbol: str,
    timeframe: str,
    max_candidates: int = 4,
) -> List[Pattern]:
    """Scan for DT / DB / Triple formations (Ch VI-A step 1: SCAN)."""
    out: List[Pattern] = []
    for inverse in (False, True):
        # Triples first: a triple is a strictly better read of the same price
        # action than the double it contains (Ch IV-D).
        out.extend(_scan(candles, pivots, cfg, atr_value, symbol, timeframe, inverse, tests=3))
        out.extend(_scan(candles, pivots, cfg, atr_value, symbol, timeframe, inverse, tests=2))
    return _dedupe(out)[:max_candidates]


def _scan(
    candles: Sequence[Candle],
    pivots: Sequence[Pivot],
    cfg: DoubleTopConfig,
    atr_value: float,
    symbol: str,
    timeframe: str,
    inverse: bool,
    tests: int,
) -> List[Pattern]:
    results: List[Pattern] = []
    size = 2 * tests - 1           # 3 pivots for a double, 5 for a triple
    if len(pivots) < size or atr_value <= 0:
        return results

    apex_kind = PivotType.LOW if inverse else PivotType.HIGH

    for start in range(len(pivots) - size + 1):
        window = list(pivots[start : start + size])
        if window[0].kind is not apex_kind:
            continue
        peaks = window[0::2]
        troughs = window[1::2]
        if len(peaks) != tests or len(troughs) != tests - 1:
            continue
        pattern = _build(
            candles, peaks, troughs, cfg, atr_value, symbol, timeframe, inverse
        )
        if pattern is not None:
            results.append(pattern)
    return results


def _build(
    candles: Sequence[Candle],
    peaks: Sequence[Pivot],
    troughs: Sequence[Pivot],
    cfg: DoubleTopConfig,
    atr_value: float,
    symbol: str,
    timeframe: str,
    inverse: bool,
) -> Optional[Pattern]:
    tests = len(peaks)
    start_index, end_index = peaks[0].index, peaks[-1].index
    bars = end_index - start_index
    if bars < _ABSOLUTE_MIN_BARS or bars > cfg.max_formation_bars:
        return None

    # Ch IV-A step 2 / Ch XVII glossary: the confirmation line is the pullback
    # extreme between tests. With two troughs (triple) the structurally
    # decisive break is through the further one.
    trough_prices = [t.price for t in troughs]
    confirmation = max(trough_prices) if inverse else min(trough_prices)
    peak_prices = [p.price for p in peaks]
    #: The manual's formula reads "|Peak - Confirmation Line|". With peaks up
    #: to 3% apart we take the tested level to be their mean.
    tested_level = sum(peak_prices) / len(peak_prices)
    height = abs(tested_level - confirmation)
    if height <= 0:
        return None

    line = horizontal_line(start_index, end_index, confirmation)
    filters: List[FilterResult] = []

    # MANDATORY — Ch IV-C: "Must follow a clear directional move — this is a
    # reversal, not a range pattern."  A range's own highs would otherwise
    # read as a double top, so the structure of the approach is checked too.
    required_trend = Trend.DOWN if inverse else Trend.UP
    leg = prior_trend_leg(candles, peaks[0].index, cfg.min_prior_trend_bars, required_trend)
    leg_ok = leg is not None and leg >= cfg.min_prior_trend_atr * atr_value
    structure_ok = prior_trend_is_established(
        candles, peaks[0].index, cfg.min_prior_trend_bars, required_trend
    )
    filters.append(
        FilterResult(
            "prior_trend",
            FilterStatus.MANDATORY,
            leg_ok and structure_ok,
            f"{cfg.min_prior_trend_bars}-bar {required_trend.value} leg="
            f"{(leg or 0.0) / atr_value:.2f} ATR (min {cfg.min_prior_trend_atr:.2f}); "
            f"structure {'confirms' if structure_ok else 'does not confirm'} the trend",
        )
    )

    # MANDATORY — Ch IV-C level proximity: "Peaks/troughs within 1-3% of each
    # other. Exact equality not required."  Tightened by a height-relative
    # bound so the rule is meaningful on low-volatility FX.
    spread = max(peak_prices) - min(peak_prices)
    pct_gap = spread / max(abs(tested_level), 1e-12)
    height_gap = spread / height
    proximity_ok = (
        pct_gap <= cfg.level_proximity_pct
        and height_gap <= cfg.level_proximity_height_frac
    )
    filters.append(
        FilterResult(
            "level_proximity",
            FilterStatus.MANDATORY,
            proximity_ok,
            f"tests {pct_gap:.2%} apart ({height_gap:.1%} of height); "
            f"limits {cfg.level_proximity_pct:.0%} / "
            f"{cfg.level_proximity_height_frac:.0%}",
        )
    )

    # MANDATORY — Ch IV-C spacing: "Two tests separated by meaningful pullback
    # (min 10% of pattern height). No pullback = consolidation."  Measured
    # against the prior impulse, the only non-circular reading.
    impulse = leg or 0.0
    min_pullback = max(
        cfg.min_pullback_frac_of_impulse * impulse, cfg.min_pullback_atr * atr_value
    )
    spacing_ok = height >= min_pullback
    filters.append(
        FilterResult(
            "pullback_spacing",
            FilterStatus.MANDATORY,
            spacing_ok,
            f"pullback {height / atr_value:.2f} ATR vs required "
            f"{min_pullback / atr_value:.2f} ATR",
        )
    )

    # MANDATORY — Ch IV-C context: "Pattern must NOT form inside a wider
    # range. DT inside a range = noise."
    context_ok, context_detail = _context_is_extreme(
        candles, peaks, end_index, cfg, inverse
    )
    filters.append(
        FilterResult("range_context", FilterStatus.MANDATORY, context_ok, context_detail)
    )

    # PREFERRED — Ch III-E style time filter; keeps micro-noise out.
    filters.append(
        FilterResult(
            "time_in_formation",
            FilterStatus.PREFERRED,
            bars >= cfg.min_formation_bars,
            f"{bars} bars (min {cfg.min_formation_bars})",
        )
    )

    # PREFERRED — Ch IV-C: "Second test on lower volume than first."
    volume_ok = _volume_declines(peaks)
    filters.append(
        FilterResult(
            "volume_decline",
            FilterStatus.MANDATORY if cfg.require_volume_decline else FilterStatus.PREFERRED,
            volume_ok,
            " > ".join(f"{p.leg_volume:.0f}" for p in peaks),
        )
    )

    # PREFERRED — symmetry of the two tests in time; a second test that
    # arrives immediately is usually a shelf, not a reversal.
    if tests >= 2:
        gaps = [peaks[i + 1].index - peaks[i].index for i in range(len(peaks) - 1)]
        balance = (min(gaps) / max(gaps)) if max(gaps) else 0.0
        filters.append(
            FilterResult(
                "test_spacing_symmetry",
                FilterStatus.PREFERRED,
                balance >= 0.5,
                f"test gaps {gaps} (balance {balance:.0%})",
            )
        )

    if any(f.blocking for f in filters):
        return None

    quality_points, quality = grade_quality(filters)

    if tests >= 3:
        ptype = PatternType.TRIPLE_BOTTOM if inverse else PatternType.TRIPLE_TOP
    else:
        ptype = PatternType.DOUBLE_BOTTOM if inverse else PatternType.DOUBLE_TOP

    # Ch X-A: "above 2nd peak for DT". Where the tests are not exactly level we
    # place invalidation beyond the most extreme test, which is the true
    # structural invalidation and never tighter than the manual's example.
    invalidation = min(peak_prices) if inverse else max(peak_prices)

    notes: List[str] = []
    if tests >= 3:
        notes.append("triple formation: +1 confluence (Ch IV-D)")

    return Pattern(
        type=ptype,
        symbol=symbol,
        timeframe=timeframe,
        pivots=[*peaks, *troughs],
        trigger_line=line,
        measured_height=height,
        structural_invalidation=invalidation,
        start_index=start_index,
        end_index=end_index,
        start_ts=candles[start_index].ts,
        end_ts=candles[end_index].ts,
        filters=filters,
        quality_points=quality_points,
        quality=quality,
        volume_profile_ok=volume_ok,
        prior_trend=required_trend,
        prior_trend_size=leg or 0.0,
        atr=atr_value,
        notes=notes,
    )


def _context_is_extreme(
    candles: Sequence[Candle],
    peaks: Sequence[Pivot],
    end_index: int,
    cfg: DoubleTopConfig,
    inverse: bool,
) -> tuple:
    """Ch IV-C context check — the formation must sit at the edge of structure.

    A double top printed in the middle of a wider range is noise; it only
    carries reversal information when it caps the range.
    """
    lo = max(0, end_index - cfg.context_lookback_bars)
    window = candles[lo : end_index + 1]
    if len(window) < 10:
        return False, "insufficient context history"
    ctx_high = max(c.high for c in window)
    ctx_low = min(c.low for c in window)
    ctx_range = ctx_high - ctx_low
    if ctx_range <= 0:
        return False, "degenerate context range"

    tested = max(p.price for p in peaks) if not inverse else min(p.price for p in peaks)
    if inverse:
        distance = (tested - ctx_low) / ctx_range
    else:
        distance = (ctx_high - tested) / ctx_range
    ok = distance <= cfg.context_extreme_tolerance
    return (
        ok,
        f"tests sit {distance:.1%} inside the {len(window)}-bar range "
        f"(max {cfg.context_extreme_tolerance:.0%})",
    )


def _volume_declines(peaks: Sequence[Pivot]) -> bool:
    """Ch IV-C — each successive test on lower volume than the one before."""
    vols = [p.leg_volume for p in peaks]
    if any(v <= 0 for v in vols):
        return False
    return all(vols[i] > vols[i + 1] for i in range(len(vols) - 1))


def _dedupe(patterns: Sequence[Pattern]) -> List[Pattern]:
    """Prefer the triple read over the double it contains (Ch IV-D)."""
    out: List[Pattern] = []
    for p in sorted(
        patterns, key=lambda x: (x.type.is_triple, x.quality, x.end_index), reverse=True
    ):
        overlapping = any(
            q.is_bearish == p.is_bearish
            and not (p.end_index < q.start_index or p.start_index > q.end_index)
            for q in out
        )
        if overlapping:
            continue
        out.append(p)
    return sorted(out, key=lambda p: p.end_index, reverse=True)
