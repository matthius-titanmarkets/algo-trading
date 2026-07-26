"""Configuration for the Titan Formation Breakout System.

Every parameter below is traceable to one of two firm documents:

* ``TFBS`` — *TFBS Master Strategy Manual, Titan Markets LLC, v1.0, April 2026*
* ``RMG`` — *The Beginner's Complete Guide to Risk Management in Prop Trading,
  2026 Edition, Titan Markets LLC*

Docstrings cite the chapter, so an auditor can tie any runtime number back to
the manual.  Parameters marked ``DERIVED`` are engineering choices required to
make a prose rule machine-executable; they are all overridable from YAML.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict, fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional


# --------------------------------------------------------------------------
# Enumerations
# --------------------------------------------------------------------------


class TraderProfile(str, Enum):
    """Which firm rule set the account trades under.

    TFBS Ch VIII-A / Ch XII-B: the proprietary desk may risk 1-2% per trade,
    while Titan Entry program participants are capped at 0.5% and must use
    Method C entries until 30 consecutive trades with positive expectancy.
    """

    PROP_DESK = "prop_desk"
    TITAN_ENTRY = "titan_entry"


class EntryMethod(str, Enum):
    """TFBS Ch VII — the three approved entry methods."""

    A_AGGRESSIVE = "A"      # enter on the confirming breakout close
    B_STANDARD = "B"        # retest + rejection signal (FIRM DEFAULT)
    C_CONSERVATIVE = "C"    # retest + follow-through candle


# --------------------------------------------------------------------------
# Pattern detection
# --------------------------------------------------------------------------


@dataclass
class SwingConfig:
    """Pivot detection used to build the swing skeleton every pattern rides on."""

    #: DERIVED — fractal half-width. A pivot high needs `lookback` lower highs
    #: on each side. 3 is the standard swing-trading fractal.
    lookback: int = 3
    #: DERIVED — ignore pivots whose excursion from the previous pivot is
    #: smaller than this multiple of ATR (noise suppression).
    min_swing_atr: float = 0.5
    #: DERIVED — how many bars of history the detector keeps in view.
    max_history: int = 500


@dataclass
class HeadShouldersConfig:
    """TFBS Ch III — Pattern Module A quality filters."""

    #: TFBS Ch III-E: "Clear established trend preceding the formation
    #: (min 20 bars)" — MANDATORY.
    min_prior_trend_bars: int = 20
    #: DERIVED — the prior trend leg must cover at least this multiple of ATR
    #: to count as "clear and established".
    min_prior_trend_atr: float = 2.0
    #: TFBS Ch III-E: "Shoulders within 20% of each other in height and
    #: duration" — PREFERRED.
    shoulder_symmetry_tolerance: float = 0.20
    #: TFBS Ch III-E: symmetry also applies to duration.
    shoulder_duration_tolerance: float = 0.20
    #: TFBS Ch III-E / Ch XIV-B: "Flat or gently sloping (< 15 degrees)".
    #: Measured in ATR-normalised space so the angle is scale free.
    max_neckline_angle_deg: float = 15.0
    #: TFBS Ch III-E: "Pattern developed over 20+ bars on primary TF".
    min_formation_bars: int = 20
    #: DERIVED — upper bound; a "pattern" spanning hundreds of bars is a range.
    max_formation_bars: int = 200
    #: DERIVED — the head must exceed both shoulders by this fraction of the
    #: pattern height, otherwise the three peaks are just a range.
    min_head_prominence: float = 0.10
    #: TFBS Ch III-C — volume should decline LS > Head > RS (PREFERRED).
    require_volume_decline: bool = False
    #: TFBS Ch XIV-B — complex H&S: extra shoulders are valid but each one
    #: "weakens slightly". Max extra shoulders tolerated per side.
    max_extra_shoulders: int = 2
    #: DERIVED — quality penalty applied per extra shoulder.
    extra_shoulder_penalty: float = 0.15


@dataclass
class DoubleTopConfig:
    """TFBS Ch IV — Pattern Module B validation criteria."""

    #: TFBS Ch IV-C: "Peaks/troughs within 1-3% of each other."
    level_proximity_pct: float = 0.03
    #: DERIVED — the literal 1-3% rule is far too loose on FX (3% of EURUSD is
    #: 375 pips), so proximity is additionally capped as a fraction of the
    #: pattern's own height. Set to a large number to trade the manual
    #: literally.
    level_proximity_height_frac: float = 0.15
    #: TFBS Ch IV-C: "Two tests separated by meaningful pullback (min 10% of
    #: pattern height). No pullback = consolidation."  Interpreted against the
    #: prior impulse leg, which is the only non-circular reading.
    min_pullback_frac_of_impulse: float = 0.10
    #: DERIVED — and the pullback must be a real structural move, not noise.
    min_pullback_atr: float = 1.0
    #: TFBS Ch IV-C: "Pattern must NOT form inside a wider range."
    context_lookback_bars: int = 100
    #: DERIVED — the pattern's peak must sit within this fraction of the
    #: context window's extreme to qualify as an edge-of-structure reversal.
    context_extreme_tolerance: float = 0.15
    #: TFBS Ch IV-C — prior trend required (this is a reversal pattern).
    min_prior_trend_bars: int = 20
    min_prior_trend_atr: float = 2.0
    #: TFBS Ch IV-C: "Second test on lower volume than first" (PREFERRED).
    require_volume_decline: bool = False
    #: TFBS Ch IV-D / Ch XIV-B: three tests = Triple Top/Bottom, +1 confluence.
    triple_confluence_bonus: int = 1
    #: DERIVED — maximum bars from first to last test.
    max_formation_bars: int = 200
    min_formation_bars: int = 15


# --------------------------------------------------------------------------
# Breakout confirmation
# --------------------------------------------------------------------------


@dataclass
class BreakoutConfig:
    """TFBS Ch V — Breakout Confirmation Protocol."""

    #: TFBS Ch V-A criterion 1 (REQUIRED): the breakout candle must CLOSE
    #: beyond the key level. Wicks alone are never confirmation.
    require_candle_close: bool = True
    #: Which screen supplies the confirming close. Ch IX puts the "breakout
    #: candle" on Screen 3 (15M/5M), which is the default; set to "pattern" to
    #: demand a full 4H/1H close beyond the level instead.
    confirm_timeframe: str = "entry"     # "entry" | "pattern"
    #: DERIVED — close must clear the level by this multiple of ATR to filter
    #: hairline closes.
    min_close_beyond_atr: float = 0.05
    #: TFBS Ch V-A criterion 2 (STRONG PREF): volume > 1.5x the 20-period avg.
    volume_surge_multiple: float = 1.5
    volume_average_period: int = 20
    #: TFBS Ch V-C / Ch VII Method B: the retest is the firm default entry.
    #: How many entry-TF bars we will wait for the retest before abandoning.
    retest_max_bars: int = 40
    #: DERIVED — price must come back within this multiple of entry-screen ATR
    #: of the broken level to count as a retest. A retest is a zone, not a
    #: price: too tight and genuine flips are missed.
    retest_tolerance_atr: float = 0.75
    #: TFBS Ch V-B / Ch V-C — a retest that closes back beyond the level in the
    #: old direction means the structural flip failed.
    retest_invalidate_on_close_back: bool = True
    #: DERIVED — how far beyond the level a close must sit before the flip is
    #: called failed. This MUST exceed ``retest_tolerance_atr``: price inside
    #: the retest zone is retesting, not invalidating, and a buffer narrower
    #: than the zone would kill setups the moment they did what Ch V-C asks
    #: them to do. The hard kill remains a close beyond the formation's own
    #: structural level (right shoulder / second peak).
    retest_invalidation_buffer_atr: float = 1.25
    #: TFBS Ch V-B false-breakout filters.
    reject_wick_only: bool = True
    reject_low_volume_break: bool = False   # STRONG PREF, not REQUIRED
    reject_choppy_context: bool = True
    #: DERIVED — "choppy macro context" is assessed on the formation, not on
    #: the break: a reversal pattern only carries information when its apex
    #: (the head, or the tested level) sits at the edge of the enclosing
    #: range. An H&S whose head is buried mid-range is an interior wiggle.
    #: Evaluated on pattern-screen bars up to the formation's end, so the
    #: post-break move cannot contaminate the measurement.
    choppy_context_lookback: int = 100
    choppy_apex_tolerance: float = 0.20
    #: TFBS Ch V-B — RSI/MACD divergence against the breakout = "elevated
    #: skepticism" (a penalty, not a hard block).
    divergence_rsi_period: int = 14
    #: TFBS Ch XIV-B — gap through the neckline on high volume is an
    #: aggressive signal; a full gap fill is invalidation.
    gap_min_atr: float = 0.5
    #: DERIVED — a confirmed pattern that never breaks expires after this many
    #: pattern-TF bars (Ch VI: "Pattern, No Break -> watchlist only").
    pattern_expiry_bars: int = 60


@dataclass
class EntryConfig:
    """TFBS Ch VII — entry methodology and execution rules."""

    #: TFBS Ch VII Method B is the FIRM DEFAULT.
    default_method: EntryMethod = EntryMethod.B_STANDARD
    #: TFBS Ch XII-B: Titan Entry traders must use Method C until 30
    #: consecutive trades with positive expectancy.
    titan_entry_method: EntryMethod = EntryMethod.C_CONSERVATIVE
    titan_entry_method_c_trades: int = 30
    #: DERIVED — rejection candle definition for Method B (pin bar / engulfing
    #: / strong wick rejection, Ch VII Method B).
    pin_bar_wick_ratio: float = 2.0          # rejection wick >= 2x body
    pin_bar_max_body_frac: float = 0.35      # body <= 35% of range
    engulfing_min_body_ratio: float = 1.0    # body engulfs prior body
    #: TFBS Ch VII Method C — follow-through candle must close beyond the
    #: rejection candle's extreme in the trade direction.
    require_followthrough_close: bool = True
    #: DERIVED — stop buffer beyond the structural point / retest wick.
    stop_buffer_atr: float = 0.15
    #: DERIVED — floor on stop distance so spread noise cannot stop us out.
    #: Applied as the larger of an ATR multiple and a spread multiple: in a
    #: quiet session ATR alone can permit a stop only a pip or two wide, which
    #: is not a structural invalidation, it is a round trip in the spread.
    min_stop_atr: float = 0.50
    min_stop_spread_multiple: float = 4.0
    #: DERIVED — and a floor relative to the *pattern* screen's volatility.
    #: The thesis being invalidated is a 4H/1H formation, so a stop scaled
    #: only to 15M noise is not really protecting it: a tick of 15M chop would
    #: close a trade whose structure is entirely intact. This keeps the Ch VII
    #: Method B advantage (tighter than the full structural stop) without
    #: manufacturing implausible 20R setups out of a one-pip stop.
    min_stop_pattern_atr_frac: float = 0.25
    #: DERIVED — cap on stop distance; wider than this and the measured move
    #: cannot realistically pay 2R.
    max_stop_atr: float = 6.0
    #: Appendix A asks "Is stop-loss at the structural invalidation point?"
    #: while Ch VII Method B places it "just beyond retest wick". The two
    #: reconcile because a failed retest *is* the invalidation of the
    #: structural flip, so the retest-wick stop is the default. Set this true
    #: to force every stop out to the pattern's own invalidation level
    #: (wider stop, worse R:R, fewer stop-outs).
    enforce_structural_stop_floor: bool = False


# --------------------------------------------------------------------------
# Confluence scoring
# --------------------------------------------------------------------------


@dataclass
class ConfluenceConfig:
    """TFBS Ch XI — Confluence Scoring System (mandatory, out of 10)."""

    #: TFBS Ch XI: "Only trades scoring 7/10 or higher are approved."
    min_score_to_trade: int = 7
    elite_score: int = 9        # 9-10 ELITE  -> full size (up to 2%)
    approved_score: int = 7     # 7-8 APPROVED -> standard size (1-1.5%)
    watchlist_score: int = 5    # 5-6 WATCHLIST -> monitor only
    #: TFBS Ch XI factor maxima.
    max_pattern_quality: int = 2
    max_breakout_strength: int = 2
    max_retest_confirm: int = 1
    max_htf_alignment: int = 2
    max_sr_confluence: int = 1
    max_rr_ratio: int = 1
    max_clean_path: int = 1
    #: TFBS Ch IV-D / Ch XIV-B — triple formation bonus.
    triple_bonus: int = 1
    #: TFBS Ch VI-B note — A+ ELITE: H&S right shoulder coinciding with the
    #: second peak of a Double Top. Highest conviction setup.
    dual_pattern_bonus: int = 1
    #: DERIVED — thresholds for the "clean path" factor: no opposing S/R level
    #: may sit within this fraction of the distance from entry to TP1.
    clean_path_obstacle_frac: float = 0.75
    #: DERIVED — "S/R confluence": an independent HTF level within this many
    #: ATR of entry or target.
    sr_confluence_atr: float = 0.75


# --------------------------------------------------------------------------
# Multi-timeframe protocol
# --------------------------------------------------------------------------


@dataclass
class MTFConfig:
    """TFBS Ch IX — Multi-Timeframe Alignment Protocol (three screens)."""

    #: Screen 1 — TREND: macro directional bias.
    trend_timeframes: List[str] = field(default_factory=lambda: ["1D", "1W"])
    #: Screen 2 — PATTERN: formation identification.
    pattern_timeframes: List[str] = field(default_factory=lambda: ["4H", "1H"])
    #: Screen 3 — ENTRY: precision trigger.
    entry_timeframes: List[str] = field(default_factory=lambda: ["15M", "5M"])
    #: TFBS Ch IX: "Trade WITH the HTF trend"; counter-trend setups score 0 on
    #: HTF alignment, which usually drags the total below the 7/10 gate.
    block_counter_trend: bool = False
    #: DERIVED — EMA pair used alongside swing structure for bias.
    bias_fast_ema: int = 20
    bias_slow_ema: int = 50
    #: DERIVED — minimum bars of HTF history before a bias is trusted.
    min_bias_bars: int = 60
    #: TFBS Ch IX: "Divergence = caution. Conflicting screens = reduce size."
    conflicting_screen_size_factor: float = 0.5


# --------------------------------------------------------------------------
# Risk management
# --------------------------------------------------------------------------


@dataclass
class RiskConfig:
    """TFBS Ch VIII + RMG sections 02-05 — the firm's hard risk parameters."""

    #: TFBS Ch VIII-A: max risk per trade 1-2% (prop desk).
    #: RMG 02: Titan default 0.50%, ceiling 1.00% on high conviction.
    max_risk_per_trade_pct: float = 2.0
    default_risk_per_trade_pct: float = 1.0
    #: TFBS Ch XII-B / RMG 02 — Titan Entry cap.
    titan_entry_risk_pct: float = 0.5
    titan_entry_max_risk_pct: float = 0.5
    #: TFBS Ch XI grade -> size mapping.
    elite_risk_pct: float = 2.0        # 9-10 ELITE
    approved_risk_pct: float = 1.25    # 7-8 APPROVED (1-1.5% band midpoint)
    #: TFBS Ch VIII-A: max aggregate open risk 5% across all positions.
    max_aggregate_open_risk_pct: float = 5.0
    #: TFBS Ch VIII-A: min reward-to-risk 2:1 — below = skip. HARD LIMIT.
    min_reward_risk: float = 2.0
    #: TFBS Ch VIII-A: max 2 trades in the same currency/sector.
    max_correlated_positions: int = 2
    #: TFBS Ch VIII-A / Ch XII-A5: daily loss limit 3% — cease trading.
    daily_loss_limit_pct: float = 3.0
    #: TFBS Ch VIII-A / Ch XII-A5: weekly loss limit 6% — reduce size or pause.
    weekly_loss_limit_pct: float = 6.0
    #: TFBS Ch VIII-A: monthly drawdown trigger 10% — mandatory CEO review.
    monthly_dd_trigger_pct: float = 10.0
    #: RMG 05 — Titan Entry daily drawdown ladder.
    dd_restricted_daily_pct: float = 2.0     # 2%-3%  -> position size cap
    dd_probation_daily_pct: float = 3.0      # 3%-4%  -> review required
    dd_suspended_daily_pct: float = 4.0      # >4%    -> suspended
    #: RMG 05 — Titan Entry max account drawdown ladder.
    dd_restricted_max_pct: float = 8.0       # 8%-10%
    dd_probation_max_pct: float = 10.0       # 10%-12%
    dd_suspended_max_pct: float = 12.0       # >12%
    #: RMG 05 — the "position size cap enforced" multiplier in RESTRICTED.
    restricted_size_factor: float = 0.5
    #: DERIVED — measure max drawdown from the equity high-water mark
    #: (trailing) or from the initial balance (static).
    max_drawdown_basis: str = "trailing"     # "trailing" | "static"
    #: DERIVED — refuse to open more than this many positions at once.
    max_open_positions: int = 5
    #: DERIVED — one position per instrument. Ch VIII-A's "2 trades same
    #: currency/sector" governs correlated *instruments*; a second position in
    #: the same instrument is an add-on, and Ch VIII-C only permits those once
    #: the trade is in profit with its stop at breakeven.
    max_positions_per_symbol: int = 1
    #: TFBS Ch XIII-B: "Trades per Week: 3-8 quality TFBS trades typical".
    #: Soft governor to stop the bot over-trading.
    max_trades_per_day: int = 4
    max_trades_per_week: int = 8
    #: TFBS Ch VIII-C — scaling rules.
    allow_scaling: bool = True
    scale_in_requires_breakeven: bool = True
    #: TFBS Ch VIII-C / Ch XII-A2 / Ch XIV-A5: absolutely prohibited.
    allow_averaging_down: bool = False


@dataclass
class TradeManagementConfig:
    """TFBS Ch X — Trade Management & Exit Architecture."""

    #: TFBS Ch X-A: "Move SL to BE when price moves 1R in your favor."
    #: RMG 03 warns against moving to BE *prematurely* — 1R is the trigger.
    breakeven_at_r: float = 1.0
    #: DERIVED — small cushion so the BE stop is not filled by spread.
    breakeven_offset_r: float = 0.05
    #: TFBS Ch X-A: "After 1.5R, trail using most recent swing or 20-EMA."
    trail_start_r: float = 1.5
    trail_method: str = "swing"          # "swing" | "ema"
    trail_ema_period: int = 20
    trail_swing_buffer_atr: float = 0.15
    #: TFBS Ch X-B — take-profit architecture.
    tp1_allocation: float = 0.50         # measured move target
    tp2_allocation: float = 0.30         # next significant S/R beyond TP1
    tp3_allocation: float = 0.20         # trailed runner
    #: DERIVED — if no distinct S/R exists beyond TP1, project TP2 at this R.
    tp2_fallback_r: float = 3.0
    #: TFBS Ch X-C — early exit triggers.
    max_duration_multiple: float = 3.0   # "open 3x expected duration"
    #: DERIVED — expected bars to TP1, counted in *entry-screen* bars. At the
    #: 15M default that is one trading day, so the Ch X-C stale-trade exit
    #: fires after three (Ch II: TFBS is a swing strategy, not a scalp).
    expected_duration_bars: int = 96
    exit_on_counter_pattern: bool = True
    exit_on_news_if_not_breakeven: bool = True
    exit_on_structural_invalidation: bool = True
    #: TFBS Ch XIV-A5: "Stop only moves in your favor. Never widen."
    allow_stop_widening: bool = False


# --------------------------------------------------------------------------
# Compliance
# --------------------------------------------------------------------------


@dataclass
class ComplianceConfig:
    """TFBS Ch XII — firm-level trading rules and compliance."""

    #: TFBS Ch XII-A1 — entry before breakout confirmation is prohibited.
    block_anticipation_trades: bool = True
    #: TFBS Ch XII-A3 — the Appendix A checklist is mandatory before execution.
    require_pretrade_checklist: bool = True
    #: TFBS Ch XII-A4 — every trade logged within 24 hours.
    require_journal: bool = True
    journal_deadline_hours: int = 24
    #: TFBS Ch XII-A6: "No entries within 15 min before or 5 min after
    #: high-impact releases."  Appendix A tightens the pre-window to 30 min;
    #: we default to the stricter of the two.
    news_blackout_before_min: int = 30
    news_blackout_after_min: int = 5
    #: TFBS Ch XII-C — escalation ladder.
    yellow_flag_violations: int = 1
    orange_flag_violations: int = 2
    orange_flag_window_days: int = 30
    red_flag_violations: int = 3
    #: DERIVED — size factor applied while ORANGE ("size reduction").
    orange_size_factor: float = 0.5
    #: TFBS Ch XII-B — Titan Entry promotion criteria.
    promotion_days: int = 90
    promotion_min_profit_factor: float = 1.5
    promotion_max_drawdown_pct: float = 6.0


# --------------------------------------------------------------------------
# Instruments / execution / journal
# --------------------------------------------------------------------------


@dataclass
class ExecutionConfig:
    """Broker-facing execution behaviour."""

    #: Assume the worst when a bar touches both stop and target.
    pessimistic_intrabar: bool = True
    slippage_atr: float = 0.02
    #: Cancel a resting retest limit order after this many entry-TF bars.
    pending_order_expiry_bars: int = 40
    #: Reject a fill that drifts far enough to break the 2:1 requirement.
    revalidate_rr_on_fill: bool = True
    dry_run: bool = True


@dataclass
class JournalConfig:
    """TFBS Ch XIII — journaling requirements."""

    enabled: bool = True
    directory: str = "journal"
    trade_log: str = "tfbs_trades.csv"
    signal_log: str = "tfbs_signals.csv"
    equity_log: str = "tfbs_equity.csv"
    #: TFBS Ch XII-A4: skipped trades are logged too ("wins, losses, BE,
    #: skipped").
    log_skipped_setups: bool = True


@dataclass
class AccountConfig:
    """The trading account this engine governs."""

    name: str = "TITAN-DESK-01"
    profile: TraderProfile = TraderProfile.PROP_DESK
    starting_balance: float = 500_000.0
    currency: str = "USD"
    #: TFBS Ch XII-B — consecutive positive-expectancy trades completed, used
    #: to decide whether a Titan Entry account may leave Method C.
    consecutive_positive_trades: int = 0


# --------------------------------------------------------------------------
# Root config
# --------------------------------------------------------------------------


@dataclass
class TitanConfig:
    """Root configuration object for the whole engine."""

    account: AccountConfig = field(default_factory=AccountConfig)
    swing: SwingConfig = field(default_factory=SwingConfig)
    head_shoulders: HeadShouldersConfig = field(default_factory=HeadShouldersConfig)
    double_top: DoubleTopConfig = field(default_factory=DoubleTopConfig)
    breakout: BreakoutConfig = field(default_factory=BreakoutConfig)
    entry: EntryConfig = field(default_factory=EntryConfig)
    confluence: ConfluenceConfig = field(default_factory=ConfluenceConfig)
    mtf: MTFConfig = field(default_factory=MTFConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    trade_management: TradeManagementConfig = field(default_factory=TradeManagementConfig)
    compliance: ComplianceConfig = field(default_factory=ComplianceConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    journal: JournalConfig = field(default_factory=JournalConfig)
    #: Symbols the bot scans. Empty means "every instrument in the catalog".
    symbols: List[str] = field(default_factory=list)
    #: DERIVED — ATR period used across the engine.
    atr_period: int = 14

    # -- construction ------------------------------------------------------

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TitanConfig":
        return _build(cls, data or {})

    def to_dict(self) -> Dict[str, Any]:
        return _to_plain(asdict(self))

    # -- profile helpers ---------------------------------------------------

    def is_titan_entry(self) -> bool:
        return self.account.profile is TraderProfile.TITAN_ENTRY

    def required_entry_method(self) -> EntryMethod:
        """TFBS Ch VII / Ch XII-B — which entry method this account must use."""
        if self.is_titan_entry():
            done = self.account.consecutive_positive_trades
            if done < self.entry.titan_entry_method_c_trades:
                return self.entry.titan_entry_method
        return self.entry.default_method

    def risk_pct_for_grade(self, grade: str) -> float:
        """TFBS Ch XI — grade to position size mapping, clamped by profile.

        ELITE (9-10) may take up to 2%; APPROVED (7-8) takes the standard
        1-1.5% band.  Titan Entry accounts are hard-capped at 0.5% regardless
        of grade (Ch XII-B).
        """
        base = {
            "ELITE": self.risk.elite_risk_pct,
            "APPROVED": self.risk.approved_risk_pct,
        }.get(grade.upper(), self.risk.default_risk_per_trade_pct)
        cap = (
            self.risk.titan_entry_max_risk_pct
            if self.is_titan_entry()
            else self.risk.max_risk_per_trade_pct
        )
        return min(base, cap)


# --------------------------------------------------------------------------
# (de)serialisation helpers
# --------------------------------------------------------------------------


_ENUMS = {
    "profile": TraderProfile,
    "default_method": EntryMethod,
    "titan_entry_method": EntryMethod,
}


def _build(cls, data: Dict[str, Any]):
    """Recursively build a dataclass tree from plain dicts, ignoring extras."""
    if not isinstance(data, dict):
        raise TypeError(f"expected mapping for {cls.__name__}, got {type(data).__name__}")
    kwargs: Dict[str, Any] = {}
    known = {f.name: f for f in fields(cls)}
    for key, value in data.items():
        if key not in known:
            raise KeyError(f"unknown config key '{key}' for {cls.__name__}")
        f = known[key]
        if is_dataclass(f.type) if isinstance(f.type, type) else False:
            kwargs[key] = _build(f.type, value)
            continue
        # dataclass fields resolved by name (string annotations)
        nested = _nested_type(cls, key)
        if nested is not None:
            kwargs[key] = _build(nested, value)
        elif key in _ENUMS and value is not None:
            kwargs[key] = _ENUMS[key](value)
        else:
            kwargs[key] = value
    return cls(**kwargs)


_NESTED = {
    "account": AccountConfig,
    "swing": SwingConfig,
    "head_shoulders": HeadShouldersConfig,
    "double_top": DoubleTopConfig,
    "breakout": BreakoutConfig,
    "entry": EntryConfig,
    "confluence": ConfluenceConfig,
    "mtf": MTFConfig,
    "risk": RiskConfig,
    "trade_management": TradeManagementConfig,
    "compliance": ComplianceConfig,
    "execution": ExecutionConfig,
    "journal": JournalConfig,
}


def _nested_type(cls, key: str):
    if cls is TitanConfig:
        return _NESTED.get(key)
    return None


def _to_plain(obj: Any) -> Any:
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, dict):
        return {k: _to_plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_plain(v) for v in obj]
    return obj


def load_config(path: Optional[str | Path] = None) -> TitanConfig:
    """Load configuration from YAML or JSON; firm defaults when path is None."""
    if path is None:
        return TitanConfig()
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"config file not found: {p}")
    text = p.read_text()
    if p.suffix.lower() in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise RuntimeError(
                "PyYAML is required to read YAML config; use a .json file instead"
            ) from exc
        data = yaml.safe_load(text) or {}
    else:
        data = json.loads(text)
    return TitanConfig.from_dict(data)
