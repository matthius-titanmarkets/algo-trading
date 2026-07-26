"""Firm-level trading rules and compliance — TFBS Ch XII.

    "These rules apply to all traders under Titan Markets LLC — proprietary
     desk and Titan Entry alike. Violations are subject to CEO escalation."
                                                             — TFBS Ch XII

Mandatory rules (Ch XII-A), each enforced somewhere in this engine:

    1. No Anticipation Trading   -> strategy.breakout (no entry before a close)
    2. No Averaging Down         -> this module + execution.manager
    3. Pre-Trade Checklist       -> strategy.checklist
    4. Journal Every Trade       -> journal.journal
    5. Respect Loss Limits       -> risk.limits
    6. No Trading During News    -> this module + data.news
    7. One Strategy              -> the engine trades TFBS setups only

Escalation ladder (Ch XII-C):

    YELLOW  single rule violation           documented warning + review
    ORANGE  two violations within 30 days   size reduction + supervision
    RED     three violations / critical     trading suspended pending CEO review
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional, Protocol, Sequence

from titan_tfbs.config import ComplianceConfig


class Rule(str, Enum):
    """TFBS Ch XII-A mandatory rules."""

    NO_ANTICIPATION = "no_anticipation_trading"
    NO_AVERAGING_DOWN = "no_averaging_down"
    CHECKLIST_REQUIRED = "pre_trade_checklist_required"
    JOURNAL_EVERY_TRADE = "journal_every_trade"
    RESPECT_LOSS_LIMITS = "respect_loss_limits"
    NO_NEWS_TRADING = "no_trading_during_news"
    ONE_STRATEGY = "one_strategy"
    RISK_PARAMETER_BREACH = "risk_parameter_breach"


class Flag(str, Enum):
    """TFBS Ch XII-C compliance escalation."""

    NONE = "NONE"
    YELLOW = "YELLOW"
    ORANGE = "ORANGE"
    RED = "RED"

    @property
    def blocks_trading(self) -> bool:
        return self is Flag.RED


@dataclass(frozen=True)
class Violation:
    rule: Rule
    ts: datetime
    detail: str
    critical: bool = False


class NewsCalendar(Protocol):
    """Anything that can answer "is a high-impact release near this time?"."""

    def blackout(
        self, ts: datetime, symbol: str, before_min: int, after_min: int
    ) -> Optional[str]:
        ...


@dataclass
class ComplianceMonitor:
    """Tracks rule violations and the resulting escalation flag."""

    config: ComplianceConfig
    violations: List[Violation] = field(default_factory=list)
    calendar: Optional[NewsCalendar] = None

    # -- violations --------------------------------------------------------

    def record(
        self, rule: Rule, ts: datetime, detail: str, critical: bool = False
    ) -> Violation:
        v = Violation(rule, ts, detail, critical)
        self.violations.append(v)
        return v

    def recent(self, now: datetime, days: Optional[int] = None) -> List[Violation]:
        window = days if days is not None else self.config.orange_flag_window_days
        cutoff = now - timedelta(days=window)
        return [v for v in self.violations if v.ts >= cutoff]

    def flag(self, now: datetime) -> Flag:
        """TFBS Ch XII-C escalation ladder."""
        recent = self.recent(now)
        if any(v.critical for v in recent):
            return Flag.RED
        count = len(recent)
        if count >= self.config.red_flag_violations:
            return Flag.RED
        if count >= self.config.orange_flag_violations:
            return Flag.ORANGE
        if count >= self.config.yellow_flag_violations:
            return Flag.YELLOW
        return Flag.NONE

    def size_factor(self, now: datetime) -> float:
        """ORANGE means "size reduction + supervised trading" (Ch XII-C)."""
        f = self.flag(now)
        if f is Flag.RED:
            return 0.0
        if f is Flag.ORANGE:
            return self.config.orange_size_factor
        return 1.0

    def can_trade(self, now: datetime) -> bool:
        return not self.flag(now).blocks_trading

    # -- Ch XII-A6 news blackout ------------------------------------------

    def news_blackout(self, ts: datetime, symbol: str) -> Optional[str]:
        """TFBS Ch XII-A6 / Appendix A.

            "No entries within 15 min before or 5 min after high-impact
             releases."                                       — Ch XII-A6
            "No high-impact news within 30 minutes?"           — Appendix A

        The stricter pre-window (30 minutes) is applied by default.
        """
        if self.calendar is None:
            return None
        return self.calendar.blackout(
            ts,
            symbol,
            self.config.news_blackout_before_min,
            self.config.news_blackout_after_min,
        )

    def news_check(self, ts: datetime, symbol: str) -> tuple:
        """Returns ``(clear, detail)`` for the Appendix A checklist item."""
        if self.calendar is None:
            return True, "no economic calendar attached — news filter unavailable"
        hit = self.news_blackout(ts, symbol)
        if hit:
            return False, hit
        return True, (
            f"no high-impact release within -"
            f"{self.config.news_blackout_before_min}/+"
            f"{self.config.news_blackout_after_min} minutes"
        )

    # -- Ch XII-A2 averaging down -----------------------------------------

    def check_averaging_down(
        self,
        ts: datetime,
        symbol: str,
        direction_sign: int,
        existing_open_price: Optional[float],
        new_price: float,
        existing_unrealized: float,
    ) -> Optional[Violation]:
        """TFBS Ch XII-A2 / Ch VIII-C / Ch XIV-A5 — adding to a loser.

            "Scaling into losers (averaging down) is strictly prohibited."
        """
        if existing_open_price is None:
            return None
        if existing_unrealized >= 0:
            return None
        worse = (
            new_price < existing_open_price
            if direction_sign > 0
            else new_price > existing_open_price
        )
        if not worse:
            return None
        return self.record(
            Rule.NO_AVERAGING_DOWN,
            ts,
            f"attempt to add to a losing {symbol} position at {new_price:.5f} "
            f"(open {existing_open_price:.5f})",
            critical=True,
        )

    # -- Ch XII-B Titan Entry ---------------------------------------------

    @staticmethod
    def promotion_ready(
        days_traded: int,
        profit_factor: float,
        max_drawdown_pct: float,
        cfg: ComplianceConfig,
    ) -> tuple:
        """TFBS Ch XII-B — promotion from Titan Entry to the proprietary desk.

            "90-day track record, Profit Factor > 1.5, max drawdown < 6%."
        """
        checks = {
            "track_record": days_traded >= cfg.promotion_days,
            "profit_factor": profit_factor > cfg.promotion_min_profit_factor,
            "max_drawdown": max_drawdown_pct < cfg.promotion_max_drawdown_pct,
        }
        return all(checks.values()), checks

    def report(self, now: datetime) -> Dict[str, object]:
        return {
            "flag": self.flag(now).value,
            "size_factor": self.size_factor(now),
            "violations_total": len(self.violations),
            "violations_in_window": len(self.recent(now)),
            "recent": [
                {"rule": v.rule.value, "ts": v.ts.isoformat(), "detail": v.detail}
                for v in self.recent(now)
            ],
        }
