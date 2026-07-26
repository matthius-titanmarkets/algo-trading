"""Loss limits and the drawdown state machine.

Two firm documents govern this file and they interlock:

**TFBS Ch VIII-A — hard limits**

    Daily Loss Limit     3%   cease trading for day
    Weekly Loss Limit    6%   reduce size or pause
    Monthly DD Trigger  10%   mandatory CEO review

Ch XII-A5 sharpens the first two: *"Respect Loss Limits: 3% daily or 6% weekly
= mandatory cessation."*

**Risk Management Guide s.05 — the Titan Entry drawdown ladder**

    Status      Daily DD    Max DD      Consequence
    Active      <= 2%       <= 8%       Trading permitted, no restriction
    Restricted  2%-3%       8%-10%      Position size cap enforced
    Probation   3%-4%       10%-12%     Review required before next session
    Suspended   > 4%        > 12%       Account suspended pending evaluation

The ladder and the hard limits agree at the 3% daily line: entering PROBATION
is the same event as breaching the Ch VIII-A daily loss limit, and both stop
trading for the session.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional

from titan_tfbs.config import RiskConfig


class AccountStatus(str, Enum):
    """Risk Management Guide s.05 drawdown structure."""

    ACTIVE = "ACTIVE"
    RESTRICTED = "RESTRICTED"
    PROBATION = "PROBATION"
    SUSPENDED = "SUSPENDED"

    @property
    def rank(self) -> int:
        return {
            AccountStatus.ACTIVE: 0,
            AccountStatus.RESTRICTED: 1,
            AccountStatus.PROBATION: 2,
            AccountStatus.SUSPENDED: 3,
        }[self]

    @property
    def can_open_new_trades(self) -> bool:
        # PROBATION requires a review "before next session"; SUSPENDED is
        # pending evaluation. Neither may open new risk unattended.
        return self in (AccountStatus.ACTIVE, AccountStatus.RESTRICTED)


@dataclass
class AccountState:
    """Balances and period anchors the limits are measured against."""

    starting_balance: float
    balance: float
    equity: float
    peak_equity: float
    day_start_balance: float
    week_start_balance: float
    month_start_balance: float
    current_day: date
    current_week: tuple            # ISO (year, week)
    current_month: tuple           # (year, month)
    trades_today: int = 0
    trades_this_week: int = 0
    realized_pnl_today: float = 0.0
    realized_pnl_week: float = 0.0
    realized_pnl_month: float = 0.0
    #: Set when a human has signed off a PROBATION review (RMG s.05) or the
    #: Ch VIII-A monthly CEO review.
    review_acknowledged_for_day: Optional[date] = None
    ceo_review_acknowledged_for_month: Optional[tuple] = None

    @classmethod
    def open_account(cls, balance: float, now: datetime) -> "AccountState":
        iso = now.isocalendar()
        return cls(
            starting_balance=balance,
            balance=balance,
            equity=balance,
            peak_equity=balance,
            day_start_balance=balance,
            week_start_balance=balance,
            month_start_balance=balance,
            current_day=now.date(),
            current_week=(iso[0], iso[1]),
            current_month=(now.year, now.month),
        )

    # -- period rollover ---------------------------------------------------

    def roll_periods(self, now: datetime) -> List[str]:
        """Advance day/week/month anchors. Returns which periods rolled."""
        rolled: List[str] = []
        iso = now.isocalendar()
        if now.date() != self.current_day:
            self.current_day = now.date()
            self.day_start_balance = self.balance
            self.trades_today = 0
            self.realized_pnl_today = 0.0
            self.review_acknowledged_for_day = None
            rolled.append("day")
        if (iso[0], iso[1]) != self.current_week:
            self.current_week = (iso[0], iso[1])
            self.week_start_balance = self.balance
            self.trades_this_week = 0
            self.realized_pnl_week = 0.0
            rolled.append("week")
        if (now.year, now.month) != self.current_month:
            self.current_month = (now.year, now.month)
            self.month_start_balance = self.balance
            self.realized_pnl_month = 0.0
            self.ceo_review_acknowledged_for_month = None
            rolled.append("month")
        return rolled

    # -- accounting --------------------------------------------------------

    def apply_realized(self, pnl: float) -> None:
        self.balance += pnl
        self.realized_pnl_today += pnl
        self.realized_pnl_week += pnl
        self.realized_pnl_month += pnl
        self.mark_equity(self.balance)

    def mark_equity(self, equity: float) -> None:
        """Update floating equity and the high-water mark."""
        self.equity = equity
        if equity > self.peak_equity:
            self.peak_equity = equity

    # -- drawdown measures -------------------------------------------------

    def daily_drawdown_pct(self) -> float:
        """Loss since the session opened, as a positive percentage."""
        if self.day_start_balance <= 0:
            return 0.0
        loss = self.day_start_balance - self.equity
        return max(0.0, loss / self.day_start_balance * 100.0)

    def weekly_drawdown_pct(self) -> float:
        if self.week_start_balance <= 0:
            return 0.0
        loss = self.week_start_balance - self.equity
        return max(0.0, loss / self.week_start_balance * 100.0)

    def monthly_drawdown_pct(self) -> float:
        if self.month_start_balance <= 0:
            return 0.0
        loss = self.month_start_balance - self.equity
        return max(0.0, loss / self.month_start_balance * 100.0)

    def max_drawdown_pct(self, basis: str = "trailing") -> float:
        """Account drawdown, from the equity high-water mark or from day one."""
        reference = self.peak_equity if basis == "trailing" else self.starting_balance
        if reference <= 0:
            return 0.0
        return max(0.0, (reference - self.equity) / reference * 100.0)


@dataclass
class LimitVerdict:
    """The current standing of the account against every firm limit."""

    status: AccountStatus
    can_trade: bool
    size_factor: float
    daily_dd_pct: float
    weekly_dd_pct: float
    monthly_dd_pct: float
    max_dd_pct: float
    reasons: List[str] = field(default_factory=list)
    breaches: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        return {
            "status": self.status.value,
            "can_trade": self.can_trade,
            "size_factor": self.size_factor,
            "daily_dd_pct": round(self.daily_dd_pct, 3),
            "weekly_dd_pct": round(self.weekly_dd_pct, 3),
            "monthly_dd_pct": round(self.monthly_dd_pct, 3),
            "max_dd_pct": round(self.max_dd_pct, 3),
            "reasons": list(self.reasons),
            "breaches": list(self.breaches),
        }


class DrawdownMonitor:
    """Evaluates the account against Ch VIII-A and the RMG s.05 ladder."""

    def __init__(self, config: RiskConfig) -> None:
        self.cfg = config

    def evaluate(self, state: AccountState) -> LimitVerdict:
        cfg = self.cfg
        daily = state.daily_drawdown_pct()
        weekly = state.weekly_drawdown_pct()
        monthly = state.monthly_drawdown_pct()
        max_dd = state.max_drawdown_pct(cfg.max_drawdown_basis)

        status = AccountStatus.ACTIVE
        reasons: List[str] = []
        breaches: List[str] = []

        # ---- RMG s.05 daily ladder --------------------------------------
        if daily > cfg.dd_suspended_daily_pct:
            status = AccountStatus.SUSPENDED
            breaches.append(
                f"daily drawdown {daily:.2f}% exceeds the "
                f"{cfg.dd_suspended_daily_pct:.0f}% suspension trigger (RMG s.05)"
            )
        elif daily >= cfg.dd_probation_daily_pct:
            status = _worse(status, AccountStatus.PROBATION)
            breaches.append(
                f"daily drawdown {daily:.2f}% is in the "
                f"{cfg.dd_probation_daily_pct:.0f}-{cfg.dd_suspended_daily_pct:.0f}% "
                f"probation band — review required before the next session (RMG s.05)"
            )
        elif daily >= cfg.dd_restricted_daily_pct:
            status = _worse(status, AccountStatus.RESTRICTED)
            reasons.append(
                f"daily drawdown {daily:.2f}% — position size cap enforced (RMG s.05)"
            )

        # ---- RMG s.05 max-drawdown ladder --------------------------------
        if max_dd > cfg.dd_suspended_max_pct:
            status = _worse(status, AccountStatus.SUSPENDED)
            breaches.append(
                f"account drawdown {max_dd:.2f}% exceeds the "
                f"{cfg.dd_suspended_max_pct:.0f}% suspension trigger (RMG s.05)"
            )
        elif max_dd >= cfg.dd_probation_max_pct:
            status = _worse(status, AccountStatus.PROBATION)
            breaches.append(
                f"account drawdown {max_dd:.2f}% is in the probation band (RMG s.05)"
            )
        elif max_dd >= cfg.dd_restricted_max_pct:
            status = _worse(status, AccountStatus.RESTRICTED)
            reasons.append(
                f"account drawdown {max_dd:.2f}% — position size cap enforced (RMG s.05)"
            )

        # ---- TFBS Ch VIII-A / Ch XII-A5 hard limits ----------------------
        daily_halt = daily >= cfg.daily_loss_limit_pct
        if daily_halt:
            breaches.append(
                f"daily loss {daily:.2f}% has reached the "
                f"{cfg.daily_loss_limit_pct:.0f}% limit — cease trading for the day "
                f"(Ch VIII-A, Ch XII-A5)"
            )
        weekly_halt = weekly >= cfg.weekly_loss_limit_pct
        if weekly_halt:
            breaches.append(
                f"weekly loss {weekly:.2f}% has reached the "
                f"{cfg.weekly_loss_limit_pct:.0f}% limit — mandatory cessation "
                f"(Ch VIII-A, Ch XII-A5)"
            )
        monthly_flag = monthly >= cfg.monthly_dd_trigger_pct
        if monthly_flag:
            breaches.append(
                f"monthly drawdown {monthly:.2f}% has reached the "
                f"{cfg.monthly_dd_trigger_pct:.0f}% trigger — mandatory CEO review "
                f"(Ch VIII-A)"
            )

        # Each gate is independent; clearing one never clears another.
        blocked_by_status = not status.can_open_new_trades
        # RMG s.05: PROBATION lifts only once the session review is signed off.
        if (
            status is AccountStatus.PROBATION
            and state.review_acknowledged_for_day == state.current_day
        ):
            blocked_by_status = False
            reasons.append("probation review acknowledged for this session")

        # Ch VIII-A: the 10% monthly trigger stands until the CEO signs it off,
        # independently of the daily ladder.
        monthly_block = (
            monthly_flag
            and state.ceo_review_acknowledged_for_month != state.current_month
        )

        can_trade = not (
            blocked_by_status or daily_halt or weekly_halt or monthly_block
        )

        size_factor = 1.0
        if not can_trade:
            size_factor = 0.0
        elif status in (AccountStatus.RESTRICTED, AccountStatus.PROBATION):
            size_factor = cfg.restricted_size_factor

        return LimitVerdict(
            status=status,
            can_trade=can_trade,
            size_factor=size_factor,
            daily_dd_pct=daily,
            weekly_dd_pct=weekly,
            monthly_dd_pct=monthly,
            max_dd_pct=max_dd,
            reasons=reasons,
            breaches=breaches,
        )


def _worse(a: AccountStatus, b: AccountStatus) -> AccountStatus:
    return a if a.rank >= b.rank else b
