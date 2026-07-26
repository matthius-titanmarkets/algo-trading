"""Confluence Scoring System (TFBS Ch XI).

    "Every setup receives a mandatory score out of 10 before execution. Only
     trades scoring 7/10 or higher are approved."               — TFBS Ch XI

    Factor              Pts   Scoring Criteria
    ------------------  ----  -------------------------------------------------
    Pattern Quality     0-2   0 = Ambiguous | 1 = Acceptable | 2 = Textbook
    Breakout Strength   0-2   0 = Wick only | 1 = Candle close | 2 = + volume
    Retest Confirm      0-1   0 = No retest | 1 = Clean retest with rejection
    HTF Alignment       0-2   0 = Counter-trend | 1 = Neutral | 2 = Aligned
    S/R Confluence      0-1   0 = No extra S/R | 1 = Key level aligns
    R:R Ratio           0-1   0 = Below 2:1 (auto-skip) | 1 = 2:1 or higher
    Clean Path          0-1   0 = Obstacles to TP1 | 1 = Clear path
"""

from __future__ import annotations

from typing import List, Optional, Sequence

from titan_tfbs.config import ConfluenceConfig, RiskConfig
from titan_tfbs.core.structure import Level, levels_between
from titan_tfbs.patterns.base import Pattern
from titan_tfbs.strategy.signals import (
    BreakoutEvent,
    ConfluenceScore,
    Direction,
    RetestEvent,
)


def score_setup(
    pattern: Pattern,
    breakout: BreakoutEvent,
    retest: Optional[RetestEvent],
    direction: Direction,
    htf_points: int,
    entry_price: float,
    stop_loss: float,
    take_profit_1: float,
    levels: Sequence[Level],
    atr_value: float,
    cfg: ConfluenceConfig,
    risk_cfg: RiskConfig,
) -> ConfluenceScore:
    """Produce the mandatory Ch XI score for a setup."""
    score = ConfluenceScore()

    # ---- Pattern Quality (0-2) ------------------------------------------
    score.pattern_quality = min(cfg.max_pattern_quality, pattern.quality_points)
    score.reasons["pattern_quality"] = (
        f"{pattern.type.value}: {pattern.preferred_passed}/{pattern.preferred_total} "
        f"preferred filters passed"
    )

    # ---- Breakout Strength (0-2) ----------------------------------------
    if breakout.wick_only:
        score.breakout_strength = 0
        score.reasons["breakout_strength"] = "wick only — not a valid break (Ch V-A)"
    elif breakout.volume_surge:
        score.breakout_strength = 2
        score.reasons["breakout_strength"] = (
            f"close beyond level on {breakout.volume_ratio:.2f}x average volume"
        )
    else:
        score.breakout_strength = 1
        if breakout.volume_ratio is None:
            score.reasons["breakout_strength"] = "candle close; no volume data on feed"
        else:
            score.reasons["breakout_strength"] = (
                f"candle close on {breakout.volume_ratio:.2f}x average volume "
                f"(no surge)"
            )
    score.breakout_strength = min(cfg.max_breakout_strength, score.breakout_strength)

    # ---- Retest Confirmation (0-1) --------------------------------------
    if retest is not None:
        score.retest_confirm = cfg.max_retest_confirm
        score.reasons["retest_confirm"] = (
            f"clean retest with {retest.rejection_kind} rejection"
            + (" + follow-through" if retest.followthrough else "")
        )
    else:
        score.reasons["retest_confirm"] = "no retest (Method A entry)"

    # ---- HTF Alignment (0-2) --------------------------------------------
    score.htf_alignment = max(0, min(cfg.max_htf_alignment, htf_points))
    score.reasons["htf_alignment"] = {
        0: "counter-trend to the higher timeframe (Ch IX)",
        1: "higher timeframe neutral or screens diverging",
        2: "all screens aligned — convergence = conviction (Ch IX)",
    }[score.htf_alignment]

    # ---- S/R Confluence (0-1) -------------------------------------------
    tolerance = cfg.sr_confluence_atr * atr_value
    trigger = pattern.trigger_price_at_ts(breakout.ts)
    extra = [
        lv
        for lv in levels
        if abs(lv.price - trigger) > tolerance
        and (
            abs(lv.price - entry_price) <= tolerance
            or abs(lv.price - take_profit_1) <= tolerance
        )
    ]
    if extra:
        score.sr_confluence = cfg.max_sr_confluence
        nearest = min(extra, key=lambda lv: abs(lv.price - entry_price))
        score.reasons["sr_confluence"] = (
            f"independent S/R at {nearest.price:.5f} ({nearest.touches} touches) "
            f"aligns with entry/target"
        )
    else:
        score.reasons["sr_confluence"] = "no independent S/R at entry or target"

    # ---- R:R Ratio (0-1) — below 2:1 is an auto-skip ---------------------
    risk = abs(entry_price - stop_loss)
    reward = abs(take_profit_1 - entry_price)
    rr = reward / risk if risk > 0 else 0.0
    if rr >= risk_cfg.min_reward_risk:
        score.rr_ratio = cfg.max_rr_ratio
        score.reasons["rr_ratio"] = f"{rr:.2f}:1 to the measured move"
    else:
        score.rr_ratio = 0
        score.reasons["rr_ratio"] = (
            f"{rr:.2f}:1 is below the firm's {risk_cfg.min_reward_risk:.0f}:1 "
            f"minimum — auto-skip (Ch VIII-A)"
        )

    # ---- Clean Path (0-1) ------------------------------------------------
    obstacles = _obstacles_to_target(
        levels, entry_price, take_profit_1, direction, tolerance, cfg
    )
    if obstacles:
        score.clean_path = 0
        score.reasons["clean_path"] = (
            f"{len(obstacles)} opposing level(s) between entry and TP1 "
            f"(nearest {obstacles[0].price:.5f})"
        )
    else:
        score.clean_path = cfg.max_clean_path
        score.reasons["clean_path"] = "no significant S/R between entry and TP1"

    # ---- Bonuses ---------------------------------------------------------
    if pattern.type.is_triple:
        score.triple_bonus = cfg.triple_bonus
        score.reasons["triple_bonus"] = "triple formation — three failed tests (Ch IV-D)"
    if pattern.dual_pattern:
        score.dual_pattern_bonus = cfg.dual_pattern_bonus
        score.reasons["dual_pattern_bonus"] = (
            f"A+ dual-pattern overlap with {pattern.dual_pattern} (Ch VI-B)"
        )
    if pattern.fractal_confluence:
        score.reasons["fractal"] = (
            f"same-direction {pattern.fractal_confluence} on another screen (Ch XIV-B)"
        )

    return score


def _obstacles_to_target(
    levels: Sequence[Level],
    entry: float,
    target: float,
    direction: Direction,
    tolerance: float,
    cfg: ConfluenceConfig,
) -> List[Level]:
    """Levels that stand between entry and TP1 and face the wrong way.

    A long is obstructed by resistance overhead; a short by support below.
    Levels sitting very close to the target itself do not count — the target
    is meant to be at structure.
    """
    span = abs(target - entry)
    if span <= 0:
        return []
    limit = entry + direction.sign * span * cfg.clean_path_obstacle_frac
    between = levels_between(levels, entry, limit, exclude_within=tolerance)
    opposing = [
        lv
        for lv in between
        if (lv.is_resistance if direction.is_long else not lv.is_resistance)
        and lv.touches >= 2
    ]
    return sorted(opposing, key=lambda lv: abs(lv.price - entry))
