"""The risk gate every TFBS signal must pass — Ch VI-A step 5 (SIZE).

Enforces, in order:

* the drawdown ladder and hard loss limits            (Ch VIII-A, RMG s.05)
* the Ch XII-C compliance flag                        (RED = no trading)
* maximum concurrent positions and trade cadence      (Ch XIII-B)
* maximum correlated exposure — 2 per currency/sector (Ch VIII-A)
* maximum aggregate open risk — 5%                    (Ch VIII-A)
* per-trade risk sized by confluence grade            (Ch XI)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from titan_tfbs.config import TitanConfig
from titan_tfbs.instruments import Instrument, shares_correlation_group
from titan_tfbs.risk.compliance import ComplianceMonitor, Flag, Rule
from titan_tfbs.risk.limits import AccountState, AccountStatus, DrawdownMonitor, LimitVerdict
from titan_tfbs.risk.sizing import PositionSizeResult, calculate_position_size
from titan_tfbs.strategy.signals import Grade, TradeSignal


@dataclass
class OpenRisk:
    """Risk currently committed to a live position."""

    trade_id: str
    symbol: str
    instrument: Instrument
    direction_sign: int
    size: float
    entry_price: float
    stop_loss: float
    risk_amount: float
    risk_pct: float
    opened_ts: datetime

    def update_stop(self, new_stop: float, value_per_point: float) -> None:
        """Recompute committed risk after a stop move (Ch X-A breakeven/trail)."""
        self.stop_loss = new_stop
        distance = (self.entry_price - new_stop) * self.direction_sign
        # A stop at or beyond breakeven commits no further capital.
        self.risk_amount = max(0.0, distance) * self.size * value_per_point


@dataclass
class RiskDecision:
    """Verdict on a single signal."""

    approved: bool
    risk_pct: float = 0.0
    risk_amount: float = 0.0
    size: float = 0.0
    reason: str = ""
    sizing: Optional[PositionSizeResult] = None
    verdict: Optional[LimitVerdict] = None
    aggregate_open_risk_pct: float = 0.0
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        return {
            "approved": self.approved,
            "risk_pct": round(self.risk_pct, 4),
            "risk_amount": round(self.risk_amount, 2),
            "size": self.size,
            "reason": self.reason,
            "aggregate_open_risk_pct": round(self.aggregate_open_risk_pct, 3),
            "notes": list(self.notes),
            "limits": self.verdict.to_dict() if self.verdict else None,
        }


class RiskManager:
    """Owns account state and authorises (or refuses) every trade."""

    def __init__(
        self,
        config: TitanConfig,
        now: datetime,
        compliance: Optional[ComplianceMonitor] = None,
    ) -> None:
        self.config = config
        self.state = AccountState.open_account(config.account.starting_balance, now)
        self.drawdown = DrawdownMonitor(config.risk)
        self.compliance = compliance or ComplianceMonitor(config.compliance)
        self.open_risk: Dict[str, OpenRisk] = {}

    # -- account bookkeeping ----------------------------------------------

    def on_time(self, now: datetime) -> List[str]:
        return self.state.roll_periods(now)

    def mark_equity(self, equity: float) -> None:
        self.state.mark_equity(equity)

    @property
    def balance(self) -> float:
        return self.state.balance

    @property
    def aggregate_open_risk(self) -> float:
        return sum(r.risk_amount for r in self.open_risk.values())

    @property
    def aggregate_open_risk_pct(self) -> float:
        if self.state.balance <= 0:
            return 100.0
        return self.aggregate_open_risk / self.state.balance * 100.0

    def limits(self) -> LimitVerdict:
        return self.drawdown.evaluate(self.state)

    # -- Ch VI-A step 5: SIZE ---------------------------------------------

    def evaluate(
        self,
        signal: TradeSignal,
        instrument: Instrument,
        now: datetime,
        size_factor: float = 1.0,
    ) -> RiskDecision:
        """Authorise and size a signal, or explain why it is refused."""
        cfg = self.config
        verdict = self.limits()
        agg = self.aggregate_open_risk_pct
        notes: List[str] = []

        def refuse(reason: str) -> RiskDecision:
            return RiskDecision(
                False, reason=reason, verdict=verdict,
                aggregate_open_risk_pct=agg, notes=notes,
            )

        # ---- 1. Drawdown ladder and hard loss limits --------------------
        if not verdict.can_trade:
            return refuse(
                f"account {verdict.status.value}: "
                + ("; ".join(verdict.breaches) or "trading halted")
            )
        if verdict.size_factor < 1.0:
            notes.append(
                f"{verdict.status.value}: size capped at "
                f"{verdict.size_factor:.0%} (RMG s.05)"
            )

        # ---- 2. Compliance flag (Ch XII-C) -------------------------------
        flag = self.compliance.flag(now)
        if flag.blocks_trading:
            return refuse(
                "RED compliance flag — trading suspended pending CEO review (Ch XII-C)"
            )
        if flag is Flag.ORANGE:
            notes.append("ORANGE flag: supervised trading at reduced size (Ch XII-C)")

        # ---- 3. Concurrency and cadence ---------------------------------
        if len(self.open_risk) >= cfg.risk.max_open_positions:
            return refuse(
                f"{len(self.open_risk)} positions already open "
                f"(max {cfg.risk.max_open_positions})"
            )
        if self.state.trades_today >= cfg.risk.max_trades_per_day:
            return refuse(
                f"{self.state.trades_today} trades already taken today "
                f"(cap {cfg.risk.max_trades_per_day}; Ch XIII-B expects 3-8 per week)"
            )
        if self.state.trades_this_week >= cfg.risk.max_trades_per_week:
            return refuse(
                f"{self.state.trades_this_week} trades already taken this week "
                f"(cap {cfg.risk.max_trades_per_week}; Ch XIII-B)"
            )

        # ---- 4a. One position per instrument (Ch VIII-C governs add-ons) --
        same_symbol = [
            r for r in self.open_risk.values() if r.symbol == signal.symbol
        ]
        if len(same_symbol) >= cfg.risk.max_positions_per_symbol:
            return refuse(
                f"already holding {len(same_symbol)} {signal.symbol} position(s); "
                f"adding requires the Ch VIII-C scaling rules (in profit, stop at "
                f"breakeven), not a fresh entry"
            )

        # ---- 4b. Correlated exposure (Ch VIII-A) -------------------------
        correlated = self.correlated_positions(instrument)
        if len(correlated) >= cfg.risk.max_correlated_positions:
            groups = ", ".join(sorted(set(instrument.correlation_groups)))
            return refuse(
                f"{len(correlated)} correlated positions already open "
                f"({groups}); firm guideline is "
                f"{cfg.risk.max_correlated_positions} per currency/sector (Ch VIII-A)"
            )

        # ---- 5. Per-trade risk from the confluence grade (Ch XI) ---------
        base_pct = cfg.risk_pct_for_grade(signal.grade.value)
        effective_pct = base_pct * verdict.size_factor * self.compliance.size_factor(now)
        effective_pct *= max(0.0, size_factor)
        if effective_pct <= 0:
            return refuse("effective risk allocation reduced to zero by firm limits")
        if size_factor < 1.0:
            notes.append(f"size reduced to {size_factor:.0%} for diverging screens (Ch IX)")

        # ---- 6. Aggregate open risk (Ch VIII-A hard limit: 5%) ----------
        headroom = cfg.risk.max_aggregate_open_risk_pct - agg
        if headroom <= 0:
            return refuse(
                f"aggregate open risk {agg:.2f}% is already at the "
                f"{cfg.risk.max_aggregate_open_risk_pct:.0f}% cap (Ch VIII-A)"
            )
        if effective_pct > headroom:
            notes.append(
                f"risk trimmed from {effective_pct:.2f}% to {headroom:.2f}% to stay "
                f"under the {cfg.risk.max_aggregate_open_risk_pct:.0f}% aggregate cap"
            )
            effective_pct = headroom

        # ---- 7. Position sizing (Ch VIII-B / Appendix B) -----------------
        sizing = calculate_position_size(
            instrument=instrument,
            account_balance=self.state.balance,
            risk_pct=effective_pct,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            account_currency=cfg.account.currency,
        )
        if not sizing.tradeable:
            return refuse(sizing.reason or "position size resolved to zero")
        notes.extend(sizing.notes)

        return RiskDecision(
            approved=True,
            risk_pct=sizing.risk_pct,
            risk_amount=sizing.risk_amount,
            size=sizing.size,
            sizing=sizing,
            verdict=verdict,
            aggregate_open_risk_pct=agg,
            notes=notes,
        )

    # -- position registry -------------------------------------------------

    def correlated_positions(self, instrument: Instrument) -> List[OpenRisk]:
        return [
            r
            for r in self.open_risk.values()
            if shares_correlation_group(r.instrument, instrument)
        ]

    def register_open(
        self,
        trade_id: str,
        signal: TradeSignal,
        instrument: Instrument,
        size: float,
        risk_amount: float,
        risk_pct: float,
        now: datetime,
        entry_price: Optional[float] = None,
        stop_loss: Optional[float] = None,
    ) -> OpenRisk:
        risk = OpenRisk(
            trade_id=trade_id,
            symbol=signal.symbol,
            instrument=instrument,
            direction_sign=signal.direction.sign,
            size=size,
            entry_price=entry_price if entry_price is not None else signal.entry_price,
            stop_loss=stop_loss if stop_loss is not None else signal.stop_loss,
            risk_amount=risk_amount,
            risk_pct=risk_pct,
            opened_ts=now,
        )
        self.open_risk[trade_id] = risk
        self.state.trades_today += 1
        self.state.trades_this_week += 1
        return risk

    def update_stop(self, trade_id: str, new_stop: float, value_per_point: float) -> None:
        risk = self.open_risk.get(trade_id)
        if risk is not None:
            risk.update_stop(new_stop, value_per_point)

    def reduce_size(self, trade_id: str, closed_size: float) -> None:
        """Partial exit (Ch X-B TP1/TP2) — committed risk falls with the size."""
        risk = self.open_risk.get(trade_id)
        if risk is None or risk.size <= 0:
            return
        fraction = max(0.0, min(1.0, closed_size / risk.size))
        risk.size = max(0.0, risk.size - closed_size)
        risk.risk_amount *= (1.0 - fraction)

    def register_close(self, trade_id: str, pnl: float, floating: float = 0.0) -> None:
        """Close out a trade. ``floating`` is the unrealized P&L still open."""
        self.open_risk.pop(trade_id, None)
        self.state.apply_realized(pnl, floating)

    # -- reviews -----------------------------------------------------------

    def acknowledge_probation_review(self, now: datetime) -> None:
        """RMG s.05 — "Review required before next session"."""
        self.state.review_acknowledged_for_day = now.date()

    def acknowledge_ceo_review(self, now: datetime) -> None:
        """Ch VIII-A — the 10% monthly drawdown CEO review."""
        self.state.ceo_review_acknowledged_for_month = (now.year, now.month)

    def report(self, now: datetime) -> Dict[str, object]:
        v = self.limits()
        return {
            "balance": round(self.state.balance, 2),
            "equity": round(self.state.equity, 2),
            "peak_equity": round(self.state.peak_equity, 2),
            "open_positions": len(self.open_risk),
            "aggregate_open_risk_pct": round(self.aggregate_open_risk_pct, 3),
            "trades_today": self.state.trades_today,
            "trades_this_week": self.state.trades_this_week,
            "limits": v.to_dict(),
            "compliance": self.compliance.report(now),
        }
