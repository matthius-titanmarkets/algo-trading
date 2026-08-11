"""Trade journal — TFBS Ch XIII.

    "Journal Every Trade: All trades — wins, losses, BE, skipped — logged
     within 24 hours."                                        — TFBS Ch XII-A4

    "Untracked trades do not exist. Data drives improvement."  — TFBS Ch II

Ch XIII-A's mandatory fields are the column set below, one row per trade:
date & time, instrument, pattern type, timeframes, entry method, levels,
confluence score with a per-factor breakdown, result in R and dollars, a chart
reference, and notes.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from titan_tfbs.config import JournalConfig
from titan_tfbs.execution.orders import ExitReason, Position
from titan_tfbs.strategy.signals import TradeSignal

#: Ch XIII-A — the mandatory journal fields, in the manual's order.
TRADE_COLUMNS = [
    "trade_id",
    "setup_ts",
    "entry_ts",
    "exit_ts",
    "instrument",
    "asset_class",
    "pattern_type",
    "htf_bias_tf",
    "pattern_tf",
    "entry_tf",
    "entry_method",
    "direction",
    "entry",
    "stop_loss",
    "tp1",
    "tp2",
    "tp3",
    "position_size",
    "risk_pct",
    "risk_amount",
    "confluence_score",
    "grade",
    "score_breakdown",
    "result",
    "actual_r",
    "pnl",
    "max_favorable_r",
    "max_adverse_r",
    "bars_open",
    "exit_reason",
    "chart_reference",
    "notes",
]

SIGNAL_COLUMNS = [
    "ts",
    "instrument",
    "pattern_type",
    "pattern_tf",
    "direction",
    "accepted",
    "grade",
    "score",
    "rr",
    "entry",
    "stop_loss",
    "tp1",
    "reason",
    "checklist_failures",
]

EQUITY_COLUMNS = [
    "ts",
    "balance",
    "equity",
    "peak_equity",
    "open_positions",
    "open_risk_pct",
    "daily_dd_pct",
    "max_dd_pct",
    "status",
]


@dataclass
class _Writer:
    path: Path
    columns: List[str]

    def __post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            with open(self.path, "w", newline="") as handle:
                csv.writer(handle).writerow(self.columns)

    def write(self, row: Dict[str, object]) -> None:
        with open(self.path, "a", newline="") as handle:
            csv.writer(handle).writerow([_fmt(row.get(c, "")) for c in self.columns])


def _fmt(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, separators=(",", ":"))
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, float):
        return f"{value:.8g}"
    return str(value)


class TradeJournal:
    """Writes the Ch XIII record set to CSV."""

    def __init__(self, config: JournalConfig, root: Optional[str | Path] = None) -> None:
        self.config = config
        base = Path(root) if root else Path(config.directory)
        self.enabled = config.enabled
        self.trades_path = base / config.trade_log
        self.signals_path = base / config.signal_log
        self.equity_path = base / config.equity_log
        self._trades: Optional[_Writer] = None
        self._signals: Optional[_Writer] = None
        self._equity: Optional[_Writer] = None
        self.records: List[Dict[str, object]] = []
        if self.enabled:
            self._trades = _Writer(self.trades_path, TRADE_COLUMNS)
            self._signals = _Writer(self.signals_path, SIGNAL_COLUMNS)
            self._equity = _Writer(self.equity_path, EQUITY_COLUMNS)

    # -- Ch XIII-A: trades -------------------------------------------------

    def log_trade(self, position: Position, asset_class: str = "") -> Dict[str, object]:
        """Log a closed trade with every mandatory field."""
        signal = position.signal
        result = _classify_result(position)
        row: Dict[str, object] = {
            "trade_id": position.id,
            "setup_ts": signal.created_ts,
            "entry_ts": position.opened_ts,
            "exit_ts": position.closed_ts,
            "instrument": signal.symbol,
            "asset_class": asset_class or position.instrument.asset_class.value,
            "pattern_type": signal.pattern.type.value,
            "htf_bias_tf": signal.trend_timeframe,
            "pattern_tf": signal.pattern_timeframe,
            "entry_tf": signal.entry_timeframe,
            "entry_method": signal.entry_method.value,
            "direction": signal.direction.value,
            "entry": position.entry_price,
            "stop_loss": position.initial_stop,
            "tp1": signal.take_profit_1,
            "tp2": signal.take_profit_2,
            "tp3": signal.take_profit_3,
            "position_size": position.initial_size,
            "risk_pct": signal.risk_pct,
            "risk_amount": signal.risk_amount,
            "confluence_score": signal.score.total,
            "grade": signal.grade.value,
            "score_breakdown": signal.score.breakdown(),
            "result": result,
            "actual_r": round(position.realized_r, 3),
            "pnl": round(position.realized_pnl, 2),
            "max_favorable_r": round(position.max_favorable_r, 3),
            "max_adverse_r": round(position.max_adverse_r, 3),
            "bars_open": position.bars_open,
            "exit_reason": position.close_reason.value if position.close_reason else "",
            # Ch XIII-A asks for an annotated chart. The engine records a stable
            # reference to the exact formation so desk tooling can attach one.
            "chart_reference": signal.pattern.signature(),
            "notes": "; ".join([*signal.notes, *position.notes]),
        }
        self.records.append(row)
        if self._trades:
            self._trades.write(row)
        return row

    # -- Ch XII-A4: skipped setups ----------------------------------------

    def log_signal(
        self,
        ts: datetime,
        symbol: str,
        accepted: bool,
        reason: str = "",
        signal: Optional[TradeSignal] = None,
        pattern_type: str = "",
        pattern_tf: str = "",
        checklist_failures: Optional[Sequence[str]] = None,
    ) -> None:
        """Log every evaluated setup, including the ones we passed on."""
        if not self.config.log_skipped_setups and not accepted:
            return
        row = {
            "ts": ts,
            "instrument": symbol,
            "pattern_type": (
                signal.pattern.type.value if signal else pattern_type
            ),
            "pattern_tf": signal.pattern_timeframe if signal else pattern_tf,
            "direction": signal.direction.value if signal else "",
            "accepted": accepted,
            "grade": signal.grade.value if signal else "",
            "score": signal.score.total if signal else "",
            "rr": round(signal.risk_reward, 2) if signal else "",
            "entry": signal.entry_price if signal else "",
            "stop_loss": signal.stop_loss if signal else "",
            "tp1": signal.take_profit_1 if signal else "",
            "reason": reason,
            "checklist_failures": list(checklist_failures or []),
        }
        if self._signals:
            self._signals.write(row)

    # -- equity curve ------------------------------------------------------

    def log_equity(
        self,
        ts: datetime,
        balance: float,
        equity: float,
        peak_equity: float,
        open_positions: int,
        open_risk_pct: float,
        daily_dd_pct: float,
        max_dd_pct: float,
        status: str,
    ) -> None:
        if not self._equity:
            return
        self._equity.write(
            {
                "ts": ts,
                "balance": round(balance, 2),
                "equity": round(equity, 2),
                "peak_equity": round(peak_equity, 2),
                "open_positions": open_positions,
                "open_risk_pct": round(open_risk_pct, 3),
                "daily_dd_pct": round(daily_dd_pct, 3),
                "max_dd_pct": round(max_dd_pct, 3),
                "status": status,
            }
        )

    # -- Ch XIII-B weekly review ------------------------------------------

    def pattern_breakdown(self) -> Dict[str, Dict[str, float]]:
        """Win rate and expectancy per pattern type — the Ch XIII-C review."""
        out: Dict[str, Dict[str, float]] = {}
        for row in self.records:
            key = str(row["pattern_type"])
            bucket = out.setdefault(
                key, {"trades": 0, "wins": 0, "r_total": 0.0, "pnl": 0.0}
            )
            bucket["trades"] += 1
            bucket["wins"] += 1 if row["result"] == "WIN" else 0
            bucket["r_total"] += float(row["actual_r"])
            bucket["pnl"] += float(row["pnl"])
        for bucket in out.values():
            n = bucket["trades"] or 1
            bucket["win_rate"] = bucket["wins"] / n * 100.0
            bucket["avg_r"] = bucket["r_total"] / n
        return out

    def score_correlation(self) -> Dict[int, Dict[str, float]]:
        """Score-to-outcome correlation, the Ch XIII-C monthly review item."""
        out: Dict[int, Dict[str, float]] = {}
        for row in self.records:
            score = int(row["confluence_score"])
            bucket = out.setdefault(score, {"trades": 0, "wins": 0, "r_total": 0.0})
            bucket["trades"] += 1
            bucket["wins"] += 1 if row["result"] == "WIN" else 0
            bucket["r_total"] += float(row["actual_r"])
        for bucket in out.values():
            n = bucket["trades"] or 1
            bucket["win_rate"] = bucket["wins"] / n * 100.0
            bucket["avg_r"] = bucket["r_total"] / n
        return out


def _classify_result(position: Position) -> str:
    """Ch XIII-A "Result" field: Win / Loss / BE."""
    if position.realized_pnl > 0:
        return "WIN"
    if position.realized_pnl < 0:
        if position.close_reason is ExitReason.BREAKEVEN_STOP:
            return "BE"
        return "LOSS"
    return "BE"
