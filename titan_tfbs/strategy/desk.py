"""The research desk — standing market analysis for the live bot (Ch IX).

Ch IX puts the firm's research and bias work on the higher timeframes: the
Daily and Weekly set the direction, the 4H and 1H carry the formations, and the
15M/5M charts exist only to time the entry. That analysis happens continuously,
whether or not a trade is available — which is exactly the part a bot normally
throws away, because only the fills are visible.

This module surfaces it. A :class:`DeskBrief` is the desk's read on the market
at a moment in time: what each screen says, which formations are being carried
through the Ch V protocol and what each one still needs, where the nearest
structure sits, what the book already holds, and how much Ch VIII-A headroom is
left to act with.

It is a *standing* view, not a trade write-up. The per-trade Ch XIII-A note in
:mod:`titan_tfbs.strategy.research` explains a decision after it is made; this
explains the market the bot is looking at before there is one — and, most of
the time, says plainly that there is nothing to do and why.

Like the research note, every line is read off live engine state rather than
recomputed, so the brief cannot disagree with what the bot is acting on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from titan_tfbs.core.structure import Level, Trend
from titan_tfbs.strategy.breakout import ARMED, BROKEN, READY, RETESTED

#: What each Ch V state is still waiting for, in the desk's words.
STATE_NEEDS = {
    ARMED: "a CLOSE through the level — Ch V-A; anticipation is a Ch XII-A1 violation",
    BROKEN: "price to return to the level and be rejected there (Ch V-C retest)",
    RETESTED: "a follow-through close beyond the rejection bar (Ch VII Method C)",
    READY: "nothing — this one is at its trigger",
}

#: Which entry method acts on which state (Ch VII).
STATE_METHOD = {
    BROKEN: "A",
    RETESTED: "B",
    READY: "C",
}


@dataclass
class FormationWatch:
    """One formation on the desk's board."""

    label: str
    timeframe: str
    direction: str
    state: str
    quality: int
    trigger: float
    distance: float
    distance_atr: float
    bars_in_state: int
    needs: str
    actionable_for: str = ""

    def render(self, px, width: int = 10) -> List[str]:
        gap = (
            f"trigger {px(self.trigger)}, price {px(abs(self.distance))} "
            f"{'above' if self.distance < 0 else 'below'} it "
            f"({self.distance_atr:.2f} ATR)"
        )
        head = (
            f"{self.label:<11} {self.timeframe:<4} {self.state:<9} "
            f"q{self.quality}/2  {gap}"
        )
        note = f"{'':<11} {'':<4} needs {self.needs}"
        if self.actionable_for:
            note += f" — Method {self.actionable_for} acts here"
        return [head, note]


@dataclass
class SymbolBrief:
    """The desk's read on one instrument."""

    symbol: str
    description: str
    price: float
    atr: float
    precision: int
    bias: str
    bias_strength: float
    bias_detail: str
    conflict: bool
    pattern_screen: str
    entry_screen: str
    screens: List[str] = field(default_factory=list)
    formations: List[FormationWatch] = field(default_factory=list)
    resistance: Optional[Level] = None
    support: Optional[Level] = None
    book: str = ""
    waiting_for: str = ""

    @property
    def has_content(self) -> bool:
        """True once the desk has something worth saying about this symbol.

        An empty ``screens`` list means no trend timeframe had the bars Ch IX
        needs for a bias yet, so the board really is blank — printing it every
        day of the warmup would be noise, not research.
        """
        return bool(self.formations) or bool(self.book) or bool(self.screens)

    def px(self, value: float) -> str:
        return f"{value:,.{self.precision}f}"

    def render(self, indent: str = "  ") -> List[str]:
        atr_pct = (self.atr / self.price * 100.0) if self.price else 0.0
        out = [
            f"{indent}{self.symbol} — {self.px(self.price)}   "
            f"ATR({self.entry_screen}) {self.px(self.atr)} ({atr_pct:.2f}%)   "
            f"{self.description}"
        ]
        pad = indent + "  "
        if self.screens:
            out.append(
                f"{pad}Screen 1  {' · '.join(self.screens)} -> bias "
                f"{self.bias.upper()} ({self.bias_strength:.2f})"
                + ("  [screens diverge — Ch IX caution]" if self.conflict else "")
            )
        else:
            # Not a blocker, and saying so would be false: with no directional
            # read the Ch XI HTF factor scores neutral (1/2) and the Appendix A
            # counter-trend item passes, so setups can still trade — they just
            # give up a confluence point.
            out.append(
                f"{pad}Screen 1  no trend timeframe has the history for a bias — "
                f"neutral, so Ch XI HTF scores 1/2 and nothing is blocked"
            )
        if self.formations:
            out.append(
                f"{pad}Screen 2  {len(self.formations)} formation(s) on the "
                f"Ch VI-B watchlist, nearest trigger first"
            )
            for watch in self.formations:
                for line in watch.render(self.px):
                    out.append(f"{pad}          {line}")
        else:
            out.append(
                f"{pad}Screen 2  {self.pattern_screen} — no valid formation on watch"
            )
        levels = []
        if self.resistance is not None:
            levels.append(
                f"resistance {self.px(self.resistance.price)} "
                f"({self.resistance.touches} touches)"
            )
        if self.support is not None:
            levels.append(
                f"support {self.px(self.support.price)} "
                f"({self.support.touches} touches)"
            )
        if levels:
            out.append(f"{pad}Levels    {'  |  '.join(levels)}")
        if self.book:
            out.append(f"{pad}Book      {self.book}")
        out.append(f"{pad}Waiting   {self.waiting_for}")
        return out

    def to_dict(self) -> Dict[str, object]:
        return {
            "symbol": self.symbol,
            "price": self.price,
            "atr": self.atr,
            "bias": self.bias,
            "bias_strength": round(self.bias_strength, 3),
            "conflict": self.conflict,
            "screens": list(self.screens),
            "formations": [
                {
                    "label": w.label,
                    "timeframe": w.timeframe,
                    "direction": w.direction,
                    "state": w.state,
                    "quality": w.quality,
                    "trigger": w.trigger,
                    "distance_atr": round(w.distance_atr, 3),
                    "bars_in_state": w.bars_in_state,
                    "needs": w.needs,
                }
                for w in self.formations
            ],
            "resistance": self.resistance.price if self.resistance else None,
            "support": self.support.price if self.support else None,
            "book": self.book,
            "waiting_for": self.waiting_for,
        }


@dataclass
class DeskBrief:
    """The whole desk at one moment: account posture plus every instrument."""

    ts: datetime
    title: str
    balance: float
    equity: float
    status: str
    can_trade: bool
    open_risk_pct: float
    risk_cap_pct: float
    trades_today: int
    trades_today_cap: int
    trades_week: int
    trades_week_cap: int
    compliance_flag: str
    breaches: List[str] = field(default_factory=list)
    symbols: List[SymbolBrief] = field(default_factory=list)

    @property
    def has_content(self) -> bool:
        return any(s.has_content for s in self.symbols)

    @property
    def headroom_pct(self) -> float:
        """Ch VIII-A room left before the aggregate cap blocks the next trade."""
        return max(0.0, self.risk_cap_pct - self.open_risk_pct)

    def render(self, indent: str = "") -> str:
        out = [
            f"{indent}{'─' * 74}",
            f"{indent}RESEARCH DESK — {self.title}   {self.ts:%Y-%m-%d %H:%M} UTC",
            f"{indent}{'─' * 74}",
            f"{indent}  account   ${self.equity:,.0f} equity "
            f"(balance ${self.balance:,.0f}) · {self.status}"
            + ("" if self.can_trade else " · TRADING HALTED"),
            f"{indent}  headroom  {self.open_risk_pct:.2f}% open of the "
            f"{self.risk_cap_pct:.0f}% Ch VIII-A cap — "
            f"{self.headroom_pct:.2f}% left to deploy",
            f"{indent}  cadence   {self.trades_today} trade(s) today "
            f"(cap {self.trades_today_cap}), {self.trades_week} this week "
            f"(cap {self.trades_week_cap})",
        ]
        if self.compliance_flag not in ("NONE", ""):
            out.append(f"{indent}  compliance {self.compliance_flag} (Ch XII-C)")
        for breach in self.breaches:
            out.append(f"{indent}  ! {breach}")
        out.append("")
        for symbol in self.symbols:
            out.extend(f"{indent}{line}" for line in symbol.render())
            out.append("")
        return "\n".join(out).rstrip()

    def to_dict(self) -> Dict[str, object]:
        return {
            "ts": self.ts.isoformat(),
            "title": self.title,
            "equity": self.equity,
            "status": self.status,
            "can_trade": self.can_trade,
            "open_risk_pct": self.open_risk_pct,
            "headroom_pct": round(self.headroom_pct, 3),
            "trades_today": self.trades_today,
            "trades_this_week": self.trades_week,
            "compliance": self.compliance_flag,
            "symbols": [s.to_dict() for s in self.symbols],
        }


# --------------------------------------------------------------------------
# builder
# --------------------------------------------------------------------------


def build_brief(bot, title: str = "session", now: Optional[datetime] = None) -> DeskBrief:
    """Read the desk's current view off a live :class:`~titan_tfbs.bot.TFBSBot`.

    Nothing here recomputes analysis: the bias, the formations and their states,
    and the levels are the objects the bot is trading from. A brief that
    disagreed with the engine would be worse than no brief at all.
    """
    cfg = bot.config
    when = now or bot.now
    report = bot.risk.report(when)
    limits = report["limits"]

    brief = DeskBrief(
        ts=when,
        title=title,
        balance=float(report["balance"]),
        equity=float(report["equity"]),
        status=str(limits["status"]),
        can_trade=bool(limits["can_trade"]),
        open_risk_pct=float(report["aggregate_open_risk_pct"]),
        risk_cap_pct=cfg.risk.max_aggregate_open_risk_pct,
        trades_today=int(report["trades_today"]),
        trades_today_cap=cfg.risk.max_trades_per_day,
        trades_week=int(report["trades_this_week"]),
        trades_week_cap=cfg.risk.max_trades_per_week,
        compliance_flag=str(report["compliance"]["flag"]),
        breaches=list(limits["breaches"]),
    )
    for symbol in bot.stores:
        brief.symbols.append(_symbol_brief(bot, symbol, brief))
    return brief


def _symbol_brief(bot, symbol: str, desk: DeskBrief) -> SymbolBrief:
    cfg = bot.config
    instrument = bot.instruments[symbol]
    state = bot.strategy.states.get(symbol)
    alignment = state.alignment if state else None
    price = bot.last_price.get(symbol, 0.0)
    atr_value = bot.atr_for(symbol) or 0.0

    out = SymbolBrief(
        symbol=symbol,
        description=instrument.description,
        price=price,
        atr=atr_value,
        precision=instrument.price_precision,
        bias=alignment.bias.value if alignment else "unknown",
        bias_strength=alignment.bias_strength if alignment else 0.0,
        bias_detail=alignment.detail if alignment else "",
        conflict=bool(alignment and alignment.conflict),
        pattern_screen=instrument.timeframes.primary_pattern_tf,
        entry_screen=instrument.timeframes.primary_entry_tf,
    )
    if alignment is not None:
        out.screens = [f"{s.timeframe} {s.trend.value}" for s in alignment.screens]

    # ---- Screen 2: what is on the board, and what each one still needs ----
    method = cfg.required_entry_method().value
    if state is not None:
        for watch in state.tracker.active:
            pattern = watch.pattern
            trigger = pattern.trigger_price_at_ts(desk.ts)
            distance = trigger - price
            out.formations.append(
                FormationWatch(
                    label=pattern.type.value,
                    timeframe=pattern.timeframe,
                    direction="short" if pattern.is_bearish else "long",
                    state=watch.state,
                    quality=pattern.quality_points,
                    trigger=trigger,
                    distance=distance,
                    distance_atr=abs(distance) / atr_value if atr_value else 0.0,
                    bars_in_state=(
                        watch.bars_since_break if watch.state != ARMED else watch.bars_armed
                    ),
                    needs=STATE_NEEDS.get(watch.state, ""),
                    actionable_for=(
                        method if STATE_METHOD.get(watch.state) == method else ""
                    ),
                )
            )
        # Nearest trigger first: that is the one that could become a trade.
        out.formations.sort(key=lambda w: w.distance_atr)
        # Nearest structure on the pattern screen, in the trade's own terms.
        levels = state.levels.get(instrument.timeframes.primary_pattern_tf, [])
        above = [lv for lv in levels if lv.price > price]
        below = [lv for lv in levels if lv.price < price]
        out.resistance = min(above, key=lambda lv: lv.price - price) if above else None
        out.support = max(below, key=lambda lv: lv.price - price) if below else None

    # ---- the book ---------------------------------------------------------
    positions = [p for p in bot.broker.open_positions() if p.signal.symbol == symbol]
    if positions:
        parts = []
        for p in positions:
            # r_at() is the same R the Ch X trade manager moves the stop on, so
            # the brief and the manager can never disagree about where we are.
            parts.append(
                f"{'long' if p.sign > 0 else 'short'} {p.size:g} @ "
                f"{out.px(p.entry_price)}, {p.r_at(price):+.2f}R, "
                f"stop {out.px(p.stop_loss)}"
            )
        out.book = "; ".join(parts)

    out.waiting_for = _waiting_for(out, desk, method)
    return out


def _waiting_for(out: SymbolBrief, desk: DeskBrief, method: str) -> str:
    """The one line a trader would actually want: what has to happen next."""
    if not desk.can_trade:
        return f"nothing — the account is {desk.status} and cannot open risk"
    if not out.formations:
        return (
            "a Ch III or Ch IV formation to complete on the "
            f"{out.pattern_screen} — a bare level break is graded F (Ch VI-B)"
        )
    ready = [w for w in out.formations if w.actionable_for]
    if ready:
        first = ready[0]
        return (
            f"{first.label} on {first.timeframe} is at the Method {method} "
            f"trigger — the Ch XI score and the Appendix A checklist decide it"
        )
    nearest = min(out.formations, key=lambda w: w.distance_atr)
    if desk.headroom_pct <= 0:
        return (
            f"headroom — {nearest.label} is {nearest.distance_atr:.2f} ATR from its "
            f"trigger but the Ch VIII-A 5% cap is full"
        )
    return (
        f"{nearest.label} on {nearest.timeframe}: {nearest.needs} "
        f"({nearest.distance_atr:.2f} ATR away)"
    )
