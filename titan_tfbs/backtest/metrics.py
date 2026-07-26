"""Performance metrics — TFBS Ch XIII-B.

    Win Rate              Target 50-60% on setups scoring 7+
    Avg R:R Achieved      Target > 2:1
    Expectancy            (Win% x Avg Win) - (Loss% x Avg Loss). Must be positive.
    Profit Factor         Gross Profit / Gross Loss. Target > 1.5
    Max Rolling Drawdown  Must stay within firm limits
    Trades per Week       3-8 quality TFBS trades typical

The same numbers drive the Ch XII-B promotion test (90 days, PF > 1.5, max
drawdown < 6%).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from statistics import mean
from typing import Dict, List, Optional, Sequence, Tuple

from titan_tfbs.execution.orders import Position

#: Ch XIII-B targets, used to render pass/fail against the firm's standards.
TARGET_WIN_RATE = (50.0, 60.0)
TARGET_AVG_R = 2.0
TARGET_PROFIT_FACTOR = 1.5
TARGET_TRADES_PER_WEEK = (3, 8)


@dataclass
class PerformanceMetrics:
    """Everything the Ch XIII-B weekly review asks for."""

    trades: int = 0
    wins: int = 0
    losses: int = 0
    breakeven: int = 0
    win_rate: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    net_pnl: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0
    expectancy_r: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    avg_r: float = 0.0
    best_r: float = 0.0
    worst_r: float = 0.0
    max_drawdown_pct: float = 0.0
    max_drawdown_amount: float = 0.0
    return_pct: float = 0.0
    starting_balance: float = 0.0
    ending_balance: float = 0.0
    trades_per_week: float = 0.0
    weeks: float = 0.0
    avg_bars_open: float = 0.0
    by_pattern: Dict[str, Dict[str, float]] = field(default_factory=dict)
    by_grade: Dict[str, Dict[str, float]] = field(default_factory=dict)
    by_exit_reason: Dict[str, int] = field(default_factory=dict)

    # -- Ch XIII-B / Ch XII-B verdicts ------------------------------------

    def meets_firm_targets(self) -> Dict[str, bool]:
        return {
            "win_rate_50_60": TARGET_WIN_RATE[0] <= self.win_rate <= TARGET_WIN_RATE[1],
            "avg_r_above_2": self.avg_r > TARGET_AVG_R,
            "expectancy_positive": self.expectancy > 0,
            "profit_factor_above_1_5": self.profit_factor > TARGET_PROFIT_FACTOR,
            "cadence_3_to_8_per_week": (
                TARGET_TRADES_PER_WEEK[0] <= self.trades_per_week <= TARGET_TRADES_PER_WEEK[1]
            ),
        }

    def promotion_ready(self, days_traded: int, max_dd_limit: float = 6.0) -> bool:
        """Ch XII-B — Titan Entry to proprietary desk."""
        return (
            days_traded >= 90
            and self.profit_factor > TARGET_PROFIT_FACTOR
            and self.max_drawdown_pct < max_dd_limit
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            "trades": self.trades,
            "wins": self.wins,
            "losses": self.losses,
            "breakeven": self.breakeven,
            "win_rate_pct": round(self.win_rate, 2),
            "net_pnl": round(self.net_pnl, 2),
            "gross_profit": round(self.gross_profit, 2),
            "gross_loss": round(self.gross_loss, 2),
            "profit_factor": round(self.profit_factor, 3),
            "expectancy": round(self.expectancy, 2),
            "expectancy_r": round(self.expectancy_r, 3),
            "avg_win": round(self.avg_win, 2),
            "avg_loss": round(self.avg_loss, 2),
            "avg_r": round(self.avg_r, 3),
            "best_r": round(self.best_r, 3),
            "worst_r": round(self.worst_r, 3),
            "max_drawdown_pct": round(self.max_drawdown_pct, 3),
            "max_drawdown_amount": round(self.max_drawdown_amount, 2),
            "return_pct": round(self.return_pct, 3),
            "starting_balance": round(self.starting_balance, 2),
            "ending_balance": round(self.ending_balance, 2),
            "trades_per_week": round(self.trades_per_week, 2),
            "avg_bars_open": round(self.avg_bars_open, 1),
            "by_pattern": self.by_pattern,
            "by_grade": self.by_grade,
            "by_exit_reason": self.by_exit_reason,
            "firm_targets": self.meets_firm_targets(),
        }

    def render(self) -> str:  # pragma: no cover - reporting
        targets = self.meets_firm_targets()
        lines = [
            "TFBS PERFORMANCE — Ch XIII-B review",
            f"  Trades              {self.trades}  "
            f"(W {self.wins} / L {self.losses} / BE {self.breakeven})",
            f"  Win rate            {self.win_rate:.1f}%   "
            f"target 50-60%   {'OK' if targets['win_rate_50_60'] else 'off target'}",
            f"  Avg R achieved      {self.avg_r:+.2f}R   "
            f"target > 2.0R   {'OK' if targets['avg_r_above_2'] else 'off target'}",
            f"  Expectancy          {self.expectancy:+,.2f} ({self.expectancy_r:+.3f}R)  "
            f"{'OK' if targets['expectancy_positive'] else 'NEGATIVE'}",
            f"  Profit factor       {self.profit_factor:.2f}   "
            f"target > 1.5    {'OK' if targets['profit_factor_above_1_5'] else 'off target'}",
            f"  Max drawdown        {self.max_drawdown_pct:.2f}% "
            f"({self.max_drawdown_amount:,.2f})",
            f"  Net P&L             {self.net_pnl:+,.2f}  "
            f"({self.return_pct:+.2f}%)",
            f"  Cadence             {self.trades_per_week:.1f} trades/week  "
            f"target 3-8      "
            f"{'OK' if targets['cadence_3_to_8_per_week'] else 'off target'}",
        ]
        if self.by_pattern:
            lines.append("  By pattern:")
            for name, stats in sorted(self.by_pattern.items()):
                lines.append(
                    f"    {name:<8} n={stats['trades']:<4.0f} "
                    f"win={stats['win_rate']:.0f}%  avgR={stats['avg_r']:+.2f}"
                )
        if self.by_grade:
            lines.append("  By grade:")
            for name, stats in sorted(self.by_grade.items()):
                lines.append(
                    f"    {name:<10} n={stats['trades']:<4.0f} "
                    f"win={stats['win_rate']:.0f}%  avgR={stats['avg_r']:+.2f}"
                )
        return "\n".join(lines)


def compute_metrics(
    positions: Sequence[Position],
    equity_curve: Sequence[Tuple[datetime, float]],
    starting_balance: float,
) -> PerformanceMetrics:
    """Build the Ch XIII-B metric set from closed trades and the equity curve."""
    m = PerformanceMetrics(starting_balance=starting_balance)
    m.ending_balance = equity_curve[-1][1] if equity_curve else starting_balance

    pnls = [p.realized_pnl for p in positions]
    rs = [p.realized_r for p in positions]
    m.trades = len(positions)
    m.wins = sum(1 for v in pnls if v > 0)
    m.losses = sum(1 for v in pnls if v < 0)
    m.breakeven = sum(1 for v in pnls if v == 0)
    m.gross_profit = sum(v for v in pnls if v > 0)
    m.gross_loss = -sum(v for v in pnls if v < 0)
    m.net_pnl = sum(pnls)

    if m.trades:
        m.win_rate = m.wins / m.trades * 100.0
        m.avg_r = mean(rs)
        m.best_r = max(rs)
        m.worst_r = min(rs)
        m.expectancy = m.net_pnl / m.trades
        m.expectancy_r = mean(rs)
        m.avg_bars_open = mean(p.bars_open for p in positions)
    wins = [v for v in pnls if v > 0]
    losses = [-v for v in pnls if v < 0]
    m.avg_win = mean(wins) if wins else 0.0
    m.avg_loss = mean(losses) if losses else 0.0
    m.profit_factor = (
        m.gross_profit / m.gross_loss
        if m.gross_loss > 0
        else (float("inf") if m.gross_profit > 0 else 0.0)
    )
    if starting_balance > 0:
        m.return_pct = m.net_pnl / starting_balance * 100.0

    m.max_drawdown_amount, m.max_drawdown_pct = _max_drawdown(equity_curve)

    if equity_curve and len(equity_curve) > 1:
        span_days = (equity_curve[-1][0] - equity_curve[0][0]).total_seconds() / 86400.0
        m.weeks = max(span_days / 7.0, 1e-9)
        m.trades_per_week = m.trades / m.weeks if m.weeks > 0 else 0.0

    m.by_pattern = _group(positions, lambda p: p.signal.pattern.type.value)
    m.by_grade = _group(positions, lambda p: p.signal.grade.value)
    for p in positions:
        key = p.close_reason.value if p.close_reason else "unknown"
        m.by_exit_reason[key] = m.by_exit_reason.get(key, 0) + 1
    return m


def _group(positions: Sequence[Position], key_fn) -> Dict[str, Dict[str, float]]:
    out: Dict[str, Dict[str, float]] = {}
    for p in positions:
        bucket = out.setdefault(
            key_fn(p), {"trades": 0.0, "wins": 0.0, "pnl": 0.0, "r_total": 0.0}
        )
        bucket["trades"] += 1
        bucket["wins"] += 1 if p.realized_pnl > 0 else 0
        bucket["pnl"] += p.realized_pnl
        bucket["r_total"] += p.realized_r
    for bucket in out.values():
        n = bucket["trades"] or 1
        bucket["win_rate"] = bucket["wins"] / n * 100.0
        bucket["avg_r"] = bucket["r_total"] / n
    return out


def _max_drawdown(curve: Sequence[Tuple[datetime, float]]) -> Tuple[float, float]:
    """Ch XIII-B "Max Rolling Drawdown", from the equity high-water mark."""
    peak = None
    worst_amount = 0.0
    worst_pct = 0.0
    for _, equity in curve:
        if peak is None or equity > peak:
            peak = equity
        if peak and peak > 0:
            drop = peak - equity
            if drop > worst_amount:
                worst_amount = drop
            pct = drop / peak * 100.0
            if pct > worst_pct:
                worst_pct = pct
    return worst_amount, worst_pct
