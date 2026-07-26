"""Pattern Module A — Head & Shoulders (TFBS Ch III).

    "The Head & Shoulders is the primary reversal formation in the TFBS
     arsenal. It signals exhaustion of a bullish trend and transition to
     bearish control."                                        — TFBS Ch III-A

The detector builds the formation from confirmed swing pivots:

    Left Shoulder -> trough -> Head (higher high) -> trough -> Right Shoulder

The two troughs define the **neckline**, which is the Ch V key level.  The
Inverse H&S is the exact mirror (Ch III-B: "All standard rules apply in
reverse").  Complex H&S with additional shoulders is supported per Ch XIV-B,
with a quality penalty because "each extra shoulder weakens slightly".
"""

from __future__ import annotations

from typing import List, Optional, Sequence

from titan_tfbs.config import HeadShouldersConfig
from titan_tfbs.core.candles import Candle
from titan_tfbs.core.structure import (
    Line,
    Pivot,
    PivotType,
    Trend,
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

#: DERIVED — structural floor. Below this a "formation" is noise regardless of
#: the Ch III-E PREFERRED 20-bar guideline.
_ABSOLUTE_MIN_BARS = 8

#: Window sizes searched, in pivots. 5 = canonical (3 peaks), 7 and 9 pick up
#: the Ch XIV-B complex variant with one or two extra shoulders.
_WINDOW_SIZES = (5, 7, 9)


def detect_head_shoulders(
    candles: Sequence[Candle],
    pivots: Sequence[Pivot],
    cfg: HeadShouldersConfig,
    atr_value: float,
    symbol: str,
    timeframe: str,
    max_candidates: int = 4,
) -> List[Pattern]:
    """Scan for H&S and Inverse H&S formations (Ch VI-A step 1: SCAN)."""
    out: List[Pattern] = []
    for inverse in (False, True):
        out.extend(
            _scan(candles, pivots, cfg, atr_value, symbol, timeframe, inverse)
        )
    out.sort(key=lambda p: (p.end_index, p.quality), reverse=True)
    return _dedupe(out)[:max_candidates]


def _scan(
    candles: Sequence[Candle],
    pivots: Sequence[Pivot],
    cfg: HeadShouldersConfig,
    atr_value: float,
    symbol: str,
    timeframe: str,
    inverse: bool,
) -> List[Pattern]:
    """Search one polarity. ``inverse=True`` looks for the bullish mirror."""
    results: List[Pattern] = []
    if len(pivots) < 5 or atr_value <= 0:
        return results

    apex_kind = PivotType.LOW if inverse else PivotType.HIGH
    max_extra = max(0, cfg.max_extra_shoulders)

    for size in _WINDOW_SIZES:
        if (size - 5) // 2 > max_extra:
            continue
        for start in range(len(pivots) - size + 1):
            window = list(pivots[start : start + size])
            if window[0].kind is not apex_kind or window[-1].kind is not apex_kind:
                continue
            peaks = window[0::2]
            troughs = window[1::2]
            if len(peaks) < 3 or len(troughs) < 2:
                continue

            head_pos = _extreme_position(peaks, inverse)
            if head_pos == 0 or head_pos == len(peaks) - 1:
                continue  # the head must be flanked on both sides

            pattern = _build(
                candles, peaks, troughs, head_pos, cfg, atr_value,
                symbol, timeframe, inverse,
            )
            if pattern is not None:
                results.append(pattern)
    return results


def _extreme_position(peaks: Sequence[Pivot], inverse: bool) -> int:
    prices = [p.price for p in peaks]
    return prices.index(min(prices) if inverse else max(prices))


def _build(
    candles: Sequence[Candle],
    peaks: Sequence[Pivot],
    troughs: Sequence[Pivot],
    head_pos: int,
    cfg: HeadShouldersConfig,
    atr_value: float,
    symbol: str,
    timeframe: str,
    inverse: bool,
) -> Optional[Pattern]:
    head = peaks[head_pos]
    left_side = peaks[:head_pos]
    right_side = peaks[head_pos + 1 :]
    if not left_side or not right_side:
        return None

    # Of several shoulders on a side, the most extreme one defines the level.
    ls = _most_extreme(left_side, inverse)
    rs = _most_extreme(right_side, inverse)

    # Ch III-A step 3: the right shoulder must FAIL to reach the head's high.
    # "This failure is the critical signal."
    if not _beyond(head.price, ls.price, inverse) or not _beyond(head.price, rs.price, inverse):
        return None

    t_before = troughs[head_pos - 1]
    t_after = troughs[head_pos]
    neckline = Line(t_before.index, t_before.price, t_after.index, t_after.price)

    neck_at_head = neckline.value_at(head.index)
    height = abs(head.price - neck_at_head)
    if height <= 0:
        return None

    angle = neckline.angle_deg(atr_value)
    steep = abs(angle) > cfg.max_neckline_angle_deg

    # Ch XIV-B: "Steep [neckline] = distorted measured move. Use head-to-
    # neckline midpoint distance."
    if steep:
        midpoint_index = (t_before.index + t_after.index) // 2
        height = abs(head.price - neckline.value_at(midpoint_index))
        if height <= 0:
            return None

    start_index, end_index = peaks[0].index, peaks[-1].index
    bars = end_index - start_index
    if bars < _ABSOLUTE_MIN_BARS or bars > cfg.max_formation_bars:
        return None

    # ---- Ch III-E quality filters --------------------------------------
    filters: List[FilterResult] = []

    # MANDATORY — clear established prior trend (min 20 bars). Both the size
    # of the leg and the structure of the window must agree, so one swing of a
    # range cannot masquerade as a trend.
    required_trend = Trend.DOWN if inverse else Trend.UP
    leg = prior_trend_leg(candles, ls.index, cfg.min_prior_trend_bars, required_trend)
    leg_ok = leg is not None and leg >= cfg.min_prior_trend_atr * atr_value
    structure_ok = prior_trend_is_established(
        candles, ls.index, cfg.min_prior_trend_bars, required_trend
    )
    filters.append(
        FilterResult(
            "prior_trend",
            FilterStatus.MANDATORY,
            leg_ok and structure_ok,
            f"{cfg.min_prior_trend_bars}-bar {required_trend.value} leg="
            f"{(leg or 0.0) / atr_value:.2f} ATR "
            f"(min {cfg.min_prior_trend_atr:.2f}); "
            f"structure {'confirms' if structure_ok else 'does not confirm'} the trend",
        )
    )

    # MANDATORY — the head must stand clear of both shoulders, otherwise the
    # three peaks are a range, not a reversal.
    shoulder_ref = _most_extreme([ls, rs], not inverse)  # the shoulder nearer the head
    prominence = abs(head.price - shoulder_ref.price) / height
    prom_ok = prominence >= cfg.min_head_prominence
    filters.append(
        FilterResult(
            "head_prominence",
            FilterStatus.MANDATORY,
            prom_ok,
            f"{prominence:.1%} of pattern height (min {cfg.min_head_prominence:.0%})",
        )
    )

    # MANDATORY — both shoulders must sit on the correct side of the neckline,
    # otherwise the "neckline" is not supporting the formation at all.
    ls_h = _signed_height(ls.price, neckline.value_at(ls.index), inverse)
    rs_h = _signed_height(rs.price, neckline.value_at(rs.index), inverse)
    geometry_ok = ls_h > 0 and rs_h > 0
    filters.append(
        FilterResult(
            "neckline_geometry",
            FilterStatus.MANDATORY,
            geometry_ok,
            f"LS {ls_h / atr_value:.2f} ATR / RS {rs_h / atr_value:.2f} ATR above neckline",
        )
    )

    # PREFERRED — shoulder symmetry in height (Ch III-E: within 20%).
    sym = abs(ls_h - rs_h) / max(abs(ls_h), abs(rs_h), 1e-12)
    filters.append(
        FilterResult(
            "shoulder_symmetry_height",
            FilterStatus.PREFERRED,
            sym <= cfg.shoulder_symmetry_tolerance,
            f"{sym:.1%} apart (tolerance {cfg.shoulder_symmetry_tolerance:.0%})",
        )
    )

    # PREFERRED — shoulder symmetry in duration (Ch III-E).
    d_left = max(1, head.index - ls.index)
    d_right = max(1, rs.index - head.index)
    dur = abs(d_left - d_right) / max(d_left, d_right)
    filters.append(
        FilterResult(
            "shoulder_symmetry_duration",
            FilterStatus.PREFERRED,
            dur <= cfg.shoulder_duration_tolerance,
            f"{d_left} vs {d_right} bars ({dur:.1%} apart)",
        )
    )

    # PREFERRED — neckline flat or gently sloping (< 15 degrees).
    filters.append(
        FilterResult(
            "neckline_slope",
            FilterStatus.PREFERRED,
            not steep,
            f"{angle:.1f}deg (max {cfg.max_neckline_angle_deg:.0f}deg, ATR-normalised)",
        )
    )

    # PREFERRED — time in formation 20+ bars on the primary TF.
    filters.append(
        FilterResult(
            "time_in_formation",
            FilterStatus.PREFERRED,
            bars >= cfg.min_formation_bars,
            f"{bars} bars (min {cfg.min_formation_bars})",
        )
    )

    # PREFERRED — Ch III-C progressive volume decline LS > Head > RS.
    volume_ok = _volume_declines(ls, head, rs)
    filters.append(
        FilterResult(
            "volume_decline",
            FilterStatus.PREFERRED if not cfg.require_volume_decline else FilterStatus.MANDATORY,
            volume_ok,
            f"LS {ls.leg_volume:.0f} > Head {head.leg_volume:.0f} > RS {rs.leg_volume:.0f}",
        )
    )

    if any(f.blocking for f in filters):
        return None

    quality_points, quality = grade_quality(filters)

    extra_shoulders = len(peaks) - 3
    if extra_shoulders > 0:
        # Ch XIV-B: valid, but "each extra shoulder weakens slightly".
        quality = max(0.0, quality - cfg.extra_shoulder_penalty * extra_shoulders)
        if quality < 0.5 and quality_points > 0:
            quality_points -= 1

    ptype = PatternType.INVERSE_HEAD_SHOULDERS if inverse else PatternType.HEAD_SHOULDERS
    notes: List[str] = []
    if extra_shoulders:
        notes.append(f"complex H&S with {extra_shoulders} extra shoulder(s) (Ch XIV-B)")
    if steep:
        notes.append("steep neckline: measured move uses head-to-midpoint distance (Ch XIV-B)")

    return Pattern(
        type=ptype,
        symbol=symbol,
        timeframe=timeframe,
        pivots=[*peaks, *troughs],
        trigger_line=neckline,
        measured_height=height,
        # Ch VII / Ch X-A: stop goes beyond the right shoulder.
        structural_invalidation=rs.price,
        start_index=start_index,
        end_index=end_index,
        start_ts=candles[start_index].ts,
        end_ts=candles[end_index].ts,
        filters=filters,
        quality_points=quality_points,
        quality=quality,
        extra_shoulders=extra_shoulders,
        volume_profile_ok=volume_ok,
        prior_trend=required_trend,
        prior_trend_size=leg or 0.0,
        atr=atr_value,
        notes=notes,
    )


def _most_extreme(pivots: Sequence[Pivot], inverse: bool) -> Pivot:
    return min(pivots, key=lambda p: p.price) if inverse else max(pivots, key=lambda p: p.price)


def _beyond(a: float, b: float, inverse: bool) -> bool:
    """Is ``a`` more extreme than ``b`` for this polarity?"""
    return a < b if inverse else a > b


def _signed_height(price: float, neckline: float, inverse: bool) -> float:
    """Positive when the shoulder sits on the pattern's own side of the neck."""
    return (neckline - price) if inverse else (price - neckline)


def _volume_declines(ls: Pivot, head: Pivot, rs: Pivot) -> bool:
    """Ch III-C — progressive volume reduction LS > Head > RS."""
    vols = [ls.leg_volume, head.leg_volume, rs.leg_volume]
    if any(v <= 0 for v in vols):
        return False   # feed carries no volume; the PREFERRED filter is unmet
    return vols[0] > vols[1] > vols[2]


def _dedupe(patterns: Sequence[Pattern]) -> List[Pattern]:
    """Keep the best formation per head, so windows do not double-report."""
    out: List[Pattern] = []
    seen_ends: set = set()
    for p in sorted(patterns, key=lambda x: (x.quality, x.end_index), reverse=True):
        key = (p.type, p.end_index)
        if key in seen_ends:
            continue
        if any(
            q.type is p.type and abs(q.end_index - p.end_index) <= 2 for q in out
        ):
            continue
        seen_ends.add(key)
        out.append(p)
    return sorted(out, key=lambda p: p.end_index, reverse=True)
