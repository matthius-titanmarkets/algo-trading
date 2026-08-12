"""Research notes — the desk's write-up of why a setup became a trade.

Ch IX puts the firm's research and bias work on the higher timeframes, and
Ch XIII-A requires the reasoning behind every trade to be recorded, not just
its levels. This module produces that write-up automatically.

The note is *derived*, never narrated separately: every sentence is built from
the same :class:`~titan_tfbs.strategy.signals.TradeSignal`,
:class:`~titan_tfbs.strategy.mtf.MTFAlignment` and
:class:`~titan_tfbs.strategy.signals.ConfluenceScore` objects the engine
actually decided on. There is no second code path that could describe a trade
the engine did not take, or describe it differently from how it was taken. If
the note says the 15M closed 0.42 ATR through the neckline on 1.8x volume,
that is the number the Ch XI breakout-strength factor was scored from.

Three levels of detail, all from one object:

    note.overview()   headline + thesis + the plan in one line   (live console)
    note.render()     the full note, section by section          (--research full)
    note.to_dict()    the same content as data                   (JSON, reports)

Rejected setups get a note too. Ch XII-A4 requires skipped trades to be logged
with the rest, and "why we passed" is the more instructive half of the record.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from titan_tfbs.config import EntryMethod, TitanConfig
from titan_tfbs.core.structure import Trend
from titan_tfbs.instruments import Instrument
from titan_tfbs.patterns.base import FilterStatus, Pattern, PatternType
from titan_tfbs.strategy.checklist import ChecklistResult
from titan_tfbs.strategy.mtf import MTFAlignment
from titan_tfbs.strategy.signals import Direction, TradeSignal

#: Plain-English names — the manual's abbreviations are not a sentence.
PATTERN_NAMES: Dict[PatternType, str] = {
    PatternType.HEAD_SHOULDERS: "Head & Shoulders",
    PatternType.INVERSE_HEAD_SHOULDERS: "Inverse Head & Shoulders",
    PatternType.DOUBLE_TOP: "Double Top",
    PatternType.DOUBLE_BOTTOM: "Double Bottom",
    PatternType.TRIPLE_TOP: "Triple Top",
    PatternType.TRIPLE_BOTTOM: "Triple Bottom",
}

#: What the formation says about the order flow behind it (Ch III-A, Ch IV-A).
#: Kept free of dashes so it reads cleanly inside the thesis sentence.
PATTERN_MEANING: Dict[PatternType, str] = {
    PatternType.HEAD_SHOULDERS:
        "three pushes higher, the middle one highest, every one of them sold "
        "into",
    PatternType.INVERSE_HEAD_SHOULDERS:
        "three pushes lower, the middle one deepest, every one of them bought",
    PatternType.DOUBLE_TOP:
        "the same ceiling rejected twice, the second attempt failing where the "
        "first did",
    PatternType.DOUBLE_BOTTOM:
        "the same floor defended twice, the second attempt holding where the "
        "first did",
    PatternType.TRIPLE_TOP:
        "the same ceiling rejected three times, so supply sits there unabsorbed",
    PatternType.TRIPLE_BOTTOM:
        "the same floor defended three times, so demand sits there unexhausted",
}

#: Ch XI factor labels, in the manual's scoring order.
SCORE_FACTORS = [
    ("pattern_quality", "Pattern quality", "max_pattern_quality"),
    ("breakout_strength", "Breakout strength", "max_breakout_strength"),
    ("retest_confirm", "Retest confirmation", "max_retest_confirm"),
    ("htf_alignment", "HTF alignment", "max_htf_alignment"),
    ("sr_confluence", "S/R confluence", "max_sr_confluence"),
    ("rr_ratio", "Reward:risk", "max_rr_ratio"),
    ("clean_path", "Clean path", "max_clean_path"),
]

#: Long form, for the Screen 3 section.
ENTRY_METHOD_TEXT = {
    EntryMethod.A_AGGRESSIVE:
        "Method A — filled on the breakout close itself (Ch VII)",
    EntryMethod.B_STANDARD:
        "Method B — filled on the retest rejection, the firm default (Ch VII)",
    EntryMethod.C_CONSERVATIVE:
        "Method C — filled only after the retest produced follow-through "
        "(Ch VII, required under Ch XII-B)",
}

#: Short form, for the thesis sentence, where a dash would collide.
ENTRY_METHOD_PHRASE = {
    EntryMethod.A_AGGRESSIVE: "on the breakout close, which is Method A",
    EntryMethod.B_STANDARD:
        "on that rejection, which is the Method B fill the firm defaults to",
    EntryMethod.C_CONSERVATIVE:
        "only after the follow-through close, which is the Method C fill "
        "Ch XII-B requires",
}


@dataclass
class ResearchSection:
    """One headed block of the note."""

    heading: str
    lines: List[str] = field(default_factory=list)


@dataclass
class ResearchNote:
    """Why the engine did — or did not — take a trade."""

    symbol: str
    ts: datetime
    taken: bool
    headline: str
    thesis: str
    plan_line: str
    sections: List[ResearchSection] = field(default_factory=list)
    caveats: List[str] = field(default_factory=list)
    invalidation: List[str] = field(default_factory=list)
    #: Populated for skipped setups: the rule that turned this one away.
    blocked_by: str = ""

    # -- rendering ---------------------------------------------------------

    def overview(self, indent: str = "    ") -> str:
        """Headline, thesis and the plan — what a trader reads at the fill."""
        out = [f"{indent}RESEARCH — {self.headline}"]
        out.extend(_wrap(self.thesis, indent + "  "))
        if self.plan_line:
            out.append(f"{indent}  {self.plan_line}")
        if self.blocked_by:
            out.append(f"{indent}  BLOCKED BY: {self.blocked_by}")
        for caveat in self.caveats[:2]:
            out.append(f"{indent}  ! {caveat}")
        if len(self.caveats) > 2:
            out.append(f"{indent}  ! ...and {len(self.caveats) - 2} more (--research full)")
        return "\n".join(out)

    def render(self, indent: str = "") -> str:
        """The whole note, section by section."""
        out = [
            f"{indent}{'=' * 74}",
            f"{indent}RESEARCH NOTE — {self.headline}",
            f"{indent}{self.ts:%Y-%m-%d %H:%M} UTC · "
            f"{'TRADE TAKEN' if self.taken else 'SETUP SKIPPED'}",
            f"{indent}{'=' * 74}",
            "",
        ]
        out.extend(_wrap(self.thesis, indent))
        for section in self.sections:
            out.append("")
            out.append(f"{indent}{section.heading}")
            out.extend(f"{indent}  {line}" for line in section.lines)
        if self.caveats:
            out.append("")
            out.append(f"{indent}WHAT IS WEAK ABOUT IT")
            out.extend(f"{indent}  ! {c}" for c in self.caveats)
        if self.invalidation:
            out.append("")
            out.append(f"{indent}WHAT WOULD INVALIDATE IT")
            out.extend(f"{indent}  - {c}" for c in self.invalidation)
        if self.blocked_by:
            out.append("")
            out.append(f"{indent}NOT TAKEN: {self.blocked_by}")
        return "\n".join(out)

    def to_markdown(self) -> str:
        """Markdown for the Ch XIII research log."""
        out = [
            f"## {self.ts:%Y-%m-%d %H:%M} UTC — {self.headline}",
            "",
            f"**{'Trade taken' if self.taken else 'Setup skipped'}.** {self.thesis}",
        ]
        if self.plan_line:
            out += ["", f"`{self.plan_line}`"]
        for section in self.sections:
            out += ["", f"**{section.heading}**", ""]
            out += [f"- {line}" for line in section.lines]
        if self.caveats:
            out += ["", "**What is weak about it**", ""]
            out += [f"- {c}" for c in self.caveats]
        if self.invalidation:
            out += ["", "**What would invalidate it**", ""]
            out += [f"- {c}" for c in self.invalidation]
        if self.blocked_by:
            out += ["", f"**Not taken:** {self.blocked_by}"]
        out.append("")
        return "\n".join(out)

    def to_dict(self) -> Dict[str, object]:
        return {
            "symbol": self.symbol,
            "ts": self.ts.isoformat(),
            "taken": self.taken,
            "headline": self.headline,
            "thesis": self.thesis,
            "plan": self.plan_line,
            "sections": [
                {"heading": s.heading, "lines": list(s.lines)} for s in self.sections
            ],
            "caveats": list(self.caveats),
            "invalidation": list(self.invalidation),
            "blocked_by": self.blocked_by,
        }


# --------------------------------------------------------------------------
# builder
# --------------------------------------------------------------------------


def build_note(
    signal: TradeSignal,
    alignment: Optional[MTFAlignment],
    config: TitanConfig,
    *,
    instrument: Optional[Instrument] = None,
    taken: bool = True,
    blocked_by: str = "",
    checklist: Optional[ChecklistResult] = None,
    fill_price: Optional[float] = None,
) -> ResearchNote:
    """Write the research note for one fully-formed signal.

    ``fill_price`` is the price actually obtained, when there is one. It is
    reported next to the planned entry rather than replacing it: the note
    records the decision, and slippage is an execution fact about it.
    """
    p = signal.pattern
    precision = instrument.price_precision if instrument else 5

    def px(value: float) -> str:
        """Format a price at the instrument's own quoted precision."""
        return f"{value:,.{precision}f}"

    note = ResearchNote(
        symbol=signal.symbol,
        ts=signal.created_ts,
        taken=taken,
        headline=(
            f"{signal.symbol} {signal.direction.value.upper()} · "
            f"{PATTERN_NAMES.get(p.type, p.type.value)} on {p.timeframe} · "
            f"{signal.grade.value} {signal.score.total}/10"
        ),
        thesis="",
        plan_line="",
        blocked_by=blocked_by,
    )

    note.sections = [
        _screen_one(signal, alignment, config),
        _screen_two(p, px),
        _screen_three(signal, px),
        _confluence(signal, config),
        _the_plan(signal, config, instrument, px, fill_price),
    ]
    note.thesis = _thesis(signal, alignment, config, px)
    note.plan_line = _plan_line(signal, instrument, px, fill_price)
    note.caveats = _caveats(signal, alignment, checklist)
    note.invalidation = _invalidation(signal, config, px)
    return note


# -- the prose -------------------------------------------------------------


def _thesis(
    signal: TradeSignal,
    alignment: Optional[MTFAlignment],
    config: TitanConfig,
    px,
) -> str:
    """Four sentences: context, formation, trigger, payoff."""
    p = signal.pattern
    b = signal.breakout
    name = PATTERN_NAMES.get(p.type, p.type.value)
    side = signal.direction.value
    parts: List[str] = []

    # 1 — context, from Screen 1.
    if alignment is None or alignment.bias is Trend.RANGE:
        parts.append(
            f"Screen 1 has no directional bias, so this is traded on the "
            f"formation alone rather than as a trend continuation (Ch IX)."
        )
    else:
        aligned = (alignment.bias is Trend.UP) == signal.is_long
        trend_word = "uptrend" if alignment.bias is Trend.UP else "downtrend"
        if aligned:
            parts.append(
                f"The {signal.trend_timeframe} read is a {trend_word} "
                f"(strength {alignment.bias_strength:.2f}), so a {side} runs "
                f"with the macro bias (Ch IX)."
            )
        else:
            parts.append(
                f"The {signal.trend_timeframe} read is a {trend_word}, so a "
                f"{side} runs against the macro bias — Appendix A blocks this "
                f"(Ch IX)."
            )

    # 2 — the formation, from Screen 2.
    quality_word = {2: "textbook", 1: "acceptable", 0: "ambiguous"}[
        signal.score.pattern_quality
    ]
    parts.append(
        f"A {name} completed on the {p.timeframe} over "
        f"{p.end_index - p.start_index} bars "
        f"({PATTERN_MEANING.get(p.type, 'a TFBS formation')}), grading "
        f"{quality_word} on {p.preferred_passed} of {p.preferred_total} "
        f"preferred filters (Ch XI pattern quality "
        f"{signal.score.pattern_quality}/{config.confluence.max_pattern_quality})."
    )

    # 3 — the trigger, from Screen 3.
    beyond = abs(b.close - b.level) / b.atr if b.atr > 0 else 0.0
    trigger = (
        f"The {b.timeframe} closed at {px(b.close)}, {beyond:.2f} ATR through "
        f"the {'neckline' if p.type.family == 'head_shoulders' else 'confirmation line'} "
        f"at {px(b.level)}"
    )
    if b.volume_surge and b.volume_ratio is not None:
        trigger += f" on {b.volume_ratio:.2f}x the {config.breakout.volume_average_period}-bar average volume"
    elif b.volume_ratio is not None:
        trigger += f" on {b.volume_ratio:.2f}x average volume (no surge)"
    if signal.retest is not None:
        trigger += (
            f", then price returned to the level and was rejected by a "
            f"{_readable(signal.retest.rejection_kind)}"
        )
        if signal.retest.followthrough:
            trigger += " with follow-through"
    parts.append(
        f"{trigger}. The fill was taken "
        f"{ENTRY_METHOD_PHRASE[signal.entry_method]}."
    )

    # 4 — the payoff.
    parts.append(
        f"The measured move projects {px(signal.take_profit_1)}, worth "
        f"{signal.risk_reward:.2f}R against the stop at {px(signal.stop_loss)}; "
        f"the firm's floor is {config.risk.min_reward_risk:.0f}:1 (Ch VIII-A)."
    )
    return " ".join(parts)


def _plan_line(
    signal: TradeSignal,
    instrument: Optional[Instrument],
    px,
    fill_price: Optional[float] = None,
) -> str:
    if signal.position_size <= 0:
        return ""
    unit = "lots" if instrument and instrument.size_step < 1 else "contracts"
    # Quote the price actually obtained when there is one, so this line agrees
    # with the fill printed beside it rather than with the pre-slippage plan.
    price = px(fill_price if fill_price is not None else signal.entry_price)
    return (
        f"PLAN: {signal.position_size:g} {unit} @ {price}, "
        f"SL {px(signal.stop_loss)}, TP1 {px(signal.take_profit_1)} — "
        f"{signal.risk_pct:.2f}% of account "
        f"({signal.risk_amount:,.0f}) at risk for {signal.risk_reward:.2f}R"
    )


# -- the sections ----------------------------------------------------------


def _screen_one(
    signal: TradeSignal, alignment: Optional[MTFAlignment], config: TitanConfig
) -> ResearchSection:
    heading = f"SCREEN 1 — TREND ({signal.trend_timeframe}) · Ch IX macro bias"
    if alignment is None:
        return ResearchSection(heading, ["no alignment read available"])

    lines = [
        f"bias {alignment.bias.value.upper()} (strength "
        f"{alignment.bias_strength:.2f}) vs a {signal.direction.value} trade"
    ]
    for screen in alignment.screens:
        lines.append(
            f"{screen.timeframe}: {screen.trend.value} "
            f"(strength {screen.strength:.2f}, {screen.bars} bars)"
        )
    if alignment.pattern_screen is not None:
        lines.append(
            f"{alignment.pattern_screen.timeframe} pattern screen: "
            f"{alignment.pattern_screen.trend.value}"
        )
    if not alignment.screens:
        lines.append(
            f"no trend timeframe had the {config.mtf.min_bias_bars} bars needed "
            f"for a bias, so Screen 1 reads neutral rather than aligned"
        )
    elif alignment.conflict:
        lines.append(
            "screens diverge — Ch IX calls for reduced size or waiting, so the "
            f"size factor is {config.mtf.conflicting_screen_size_factor:.0%}"
        )
    else:
        lines.append("screens converge — Ch IX: \"convergence = conviction\"")
    return ResearchSection(heading, lines)


def _screen_two(pattern: Pattern, px) -> ResearchSection:
    heading = (
        f"SCREEN 2 — PATTERN ({pattern.timeframe}) · Ch III / Ch IV formation"
    )
    prior = f"prior trend: {pattern.prior_trend.value}"
    if pattern.atr > 0:
        prior += f" over {pattern.prior_trend_size / pattern.atr:.1f} ATR"
    lines = [
        f"{PATTERN_NAMES.get(pattern.type, pattern.type.value)}, "
        f"{pattern.end_index - pattern.start_index} bars, "
        f"{pattern.start_ts:%Y-%m-%d %H:%M} to {pattern.end_ts:%Y-%m-%d %H:%M}",
        prior,
        f"key level {px(pattern.trigger_line.value_at(pattern.end_index))}, "
        f"measured height {px(pattern.measured_height)}, structural "
        f"invalidation {px(pattern.structural_invalidation)}",
    ]
    for f in pattern.filters:
        mark = "pass" if f.passed else ("FAIL" if f.blocking else "miss")
        lines.append(f"[{f.status.value}] {mark:<4} {f.name}: {f.detail}")
    if pattern.dual_pattern:
        lines.append(
            f"A+ overlap with a {pattern.dual_pattern} on the same bars (Ch VI-B)"
        )
    if pattern.fractal_confluence:
        lines.append(
            f"same-direction {pattern.fractal_confluence} on another screen "
            f"(Ch XIV-B fractal case)"
        )
    if pattern.choppy_context:
        lines.append(
            "apex sits inside a wider range rather than capping it — Ch V-B "
            "choppy macro context"
        )
    return ResearchSection(heading, lines)


def _screen_three(signal: TradeSignal, px) -> ResearchSection:
    b = signal.breakout
    heading = (
        f"SCREEN 3 — ENTRY ({signal.entry_timeframe}) · Ch V break, Ch VII trigger"
    )
    beyond = abs(b.close - b.level) / b.atr if b.atr > 0 else 0.0
    if b.volume_ratio is None:
        volume = "volume: no volume data on this feed"
    elif b.volume_surge:
        volume = (
            f"volume: {b.volume_ratio:.2f}x the average — surge confirmed "
            f"(Ch V-A criterion 2)"
        )
    else:
        volume = (
            f"volume: {b.volume_ratio:.2f}x the average — below the 1.5x surge "
            f"threshold, which Ch V-A makes a strong preference, not a rule"
        )
    lines = [
        f"break: {b.timeframe} close {px(b.close)} vs level {px(b.level)} "
        f"({beyond:.2f} ATR beyond) — Ch V-A criterion 1 met on a CLOSING basis",
        volume,
    ]
    if b.pattern_tf_confirmed:
        lines.append(
            "the pattern timeframe has also closed beyond the level — the "
            "strongest form of the break"
        )
    if b.gapped:
        lines.append(f"gapped through the level by {px(b.gap_size)} (Ch XIV-B)")
    if b.divergence:
        lines.append("momentum diverges from price — Ch V-B skepticism filter")
    if signal.retest is not None:
        r = signal.retest
        lines.append(
            f"retest: price returned to {px(r.wick_price)} and was rejected by a "
            f"{_readable(r.rejection_kind)} (strength {r.rejection_strength:.2f})"
            + (" with follow-through" if r.followthrough else "")
        )
        lines.append(
            "the broken level held as support/resistance — Ch V-C structural flip"
        )
    else:
        lines.append("retest: none — this is a Method A entry on the break close")
    lines.append(ENTRY_METHOD_TEXT[signal.entry_method])
    for n in b.notes:
        lines.append(n)
    return ResearchSection(heading, lines)


def _confluence(signal: TradeSignal, config: TitanConfig) -> ResearchSection:
    score = signal.score
    cfg = config.confluence
    heading = (
        f"CONFLUENCE — Ch XI · {score.total}/10 -> {signal.grade.value} "
        f"(gate is {cfg.min_score_to_trade}/10)"
    )
    # Padded for the console; the dash keeps the columns legible after
    # markdown collapses the runs of spaces.
    lines: List[str] = []
    for key, label, cap_attr in SCORE_FACTORS:
        points = getattr(score, key)
        cap = getattr(cfg, cap_attr)
        reason = score.reasons.get(key, "")
        lines.append(f"{points}/{cap}  {label:<20} — {reason}")
    for bonus, key in (
        (score.triple_bonus, "triple_bonus"),
        (score.dual_pattern_bonus, "dual_pattern_bonus"),
    ):
        if bonus:
            lines.append(f"+{bonus}    {'bonus':<20} — {score.reasons.get(key, '')}")
    if "fractal" in score.reasons:
        lines.append(f"      {'note':<20} — {score.reasons['fractal']}")
    return ResearchSection(heading, lines)


def _the_plan(
    signal: TradeSignal,
    config: TitanConfig,
    instrument: Optional[Instrument],
    px,
    fill_price: Optional[float] = None,
) -> ResearchSection:
    tm = config.trade_management
    heading = "THE PLAN — Ch VIII sizing, Ch X exits"
    stop_distance = signal.stop_distance
    lines = [
        f"entry {px(signal.entry_price)} ({signal.entry_method.value}), "
        f"stop {px(signal.stop_loss)} — {px(stop_distance)} away",
    ]
    if fill_price is not None and abs(fill_price - signal.entry_price) > 1e-12:
        slip = abs(fill_price - signal.entry_price)
        lines.append(
            f"filled at {px(fill_price)} — {px(slip)} of slippage against the "
            f"plan ({slip / stop_distance:.2f}R)"
        )
    if signal.position_size > 0:
        lines.append(
            f"size {signal.position_size:g} at {signal.risk_pct:.2f}% risk = "
            f"{signal.risk_amount:,.2f} {config.account.currency}"
            + (
                f" ({instrument.value_per_point:,.2f}/point)"
                if instrument
                else ""
            )
        )
    else:
        lines.append("size not calculated — the setup did not reach the SIZE step")
    lines += [
        f"TP1 {px(signal.take_profit_1)} ({signal.rr_to(signal.take_profit_1):.2f}R, "
        f"{tm.tp1_allocation:.0%}) — the measured move (Ch III-D / Ch IV-D)",
        f"TP2 {px(signal.take_profit_2)} ({signal.rr_to(signal.take_profit_2):.2f}R, "
        f"{tm.tp2_allocation:.0%}) — next significant S/R (Ch X-B)",
    ]
    if signal.take_profit_3:
        lines.append(
            f"TP3 {px(signal.take_profit_3)} "
            f"({signal.rr_to(signal.take_profit_3):.2f}R, {tm.tp3_allocation:.0%}) "
            f"— the trailed runner (Ch X-B)"
        )
    lines += [
        f"stop to breakeven at {tm.breakeven_at_r:.1f}R, trailing on the "
        f"{tm.trail_method} from {tm.trail_start_r:.1f}R (Ch X-A)",
    ]
    # `signal.notes` carries the breakout's own notes too; those already
    # appear under Screen 3, so only the sizing and stop notes are new here.
    for n in signal.notes:
        if n and n not in signal.breakout.notes:
            lines.append(n)
    return ResearchSection(heading, lines)


# -- honesty ---------------------------------------------------------------


def _caveats(
    signal: TradeSignal,
    alignment: Optional[MTFAlignment],
    checklist: Optional[ChecklistResult],
) -> List[str]:
    """What is weak about the setup — the note is not a sales pitch."""
    out: List[str] = []
    p, b, score = signal.pattern, signal.breakout, signal.score

    for f in p.filters:
        if f.status is FilterStatus.PREFERRED and not f.passed:
            out.append(f"preferred filter missed — {f.name}: {f.detail}")
    if b.volume_ratio is not None and not b.volume_surge:
        out.append(
            f"breakout volume {b.volume_ratio:.2f}x is below the Ch V-A 1.5x "
            f"surge threshold"
        )
    if b.divergence:
        out.append("momentum divergence against the break (Ch V-B)")
    if b.choppy_context or p.choppy_context:
        out.append("the formation sits in a choppy macro context (Ch V-B)")
    if score.clean_path == 0:
        out.append(score.reasons.get("clean_path", "obstacles between entry and TP1"))
    if score.sr_confluence == 0:
        out.append("no independent S/R backs the entry or the target (Ch XI)")
    if alignment is not None and alignment.conflict:
        out.append("Screen 1 and Screen 2 disagree — Ch IX: \"divergence = caution\"")
    if p.extra_shoulders:
        out.append(
            f"{p.extra_shoulders} extra shoulder(s) — Ch XIV-B: each one weakens "
            f"the formation"
        )
    if checklist is not None:
        for item in checklist.preferred_missed:
            out.append(f"Appendix A [P] missed — {item.text} ({item.detail})")
    return out


def _invalidation(signal: TradeSignal, config: TitanConfig, px) -> List[str]:
    """The exits that are already armed the moment the position opens."""
    p = signal.pattern
    tm = config.trade_management
    buffer = config.breakout.retest_invalidation_buffer_atr * signal.breakout.atr
    level = signal.breakout.level
    reclaim = level - buffer if signal.is_long else level + buffer

    # Which invalidation the stop actually sits on decides how it is described.
    # Method A places it beyond the formation's own invalidation; Methods B and
    # C place it beyond the retest wick, which is tighter — a failed retest is
    # the invalidation of the structural flip rather than of the formation.
    beyond_formation = (
        signal.stop_loss <= p.structural_invalidation
        if signal.is_long
        else signal.stop_loss >= p.structural_invalidation
    )
    if beyond_formation:
        stop_line = (
            f"stop at {px(signal.stop_loss)} sits beyond the formation's own "
            f"invalidation ({px(p.structural_invalidation)}), so being stopped "
            f"means the thesis was wrong, not merely early"
        )
    else:
        stop_line = (
            f"stop at {px(signal.stop_loss)} sits just beyond the retest wick, "
            f"inside the formation's own invalidation at "
            f"{px(p.structural_invalidation)} — a failed retest is the "
            f"invalidation of the structural flip this trade is built on "
            f"(Ch VII Method {signal.entry_method.value})"
        )
    out = [
        stop_line,
        f"a close back through {px(reclaim)} reclaims the broken level and exits "
        f"the trade (Ch X-C structural invalidation)",
    ]
    if tm.exit_on_counter_pattern:
        out.append(
            "a confirmed counter-pattern on a lower timeframe exits the trade "
            "(Ch X-C)"
        )
    if tm.exit_on_news_if_not_breakeven:
        out.append(
            "high-impact news before the stop reaches breakeven exits the trade "
            "(Ch X-C)"
        )
    out.append(
        f"no progress after {int(tm.max_duration_multiple * tm.expected_duration_bars)} "
        f"entry-screen bars closes it as dead money (Ch X-C)"
    )
    return out


def _readable(name: str) -> str:
    """`strong_wick` -> `strong wick`, for prose rather than field names."""
    return name.replace("_", " ")


def _wrap(text: str, indent: str = "", width: int = 78) -> List[str]:
    """Wrap prose to a terminal width without pulling in textwrap's defaults."""
    out: List[str] = []
    line = indent
    for word in text.split():
        if len(line) + len(word) + 1 > width and line.strip():
            out.append(line.rstrip())
            line = indent
        line += word + " "
    if line.strip():
        out.append(line.rstrip())
    return out
