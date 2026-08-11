#!/usr/bin/env python3
"""Run a portfolio backtest across the firm's Ch I universe and dump results.

Emits a single JSON document consumed by ``scripts/build_report.py`` (PDF) and
``scripts/build_workbook.py`` (XLSX), so both documents describe exactly the
same run rather than two independent ones.

    python scripts/firm_backtest.py --out results.json
    python scripts/firm_backtest.py --data ./data --out results.json

Symbols are replayed INTERLEAVED in timestamp order, not one after another.
That matters: Ch VIII-A's 5% aggregate open risk and the 2-per-sector
correlated cap are portfolio rules, and they only bind correctly when symbols
compete for the same headroom in the order the market delivered them.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from titan_tfbs.backtest.metrics import compute_metrics
from titan_tfbs.bot import TFBSBot
from titan_tfbs.config import TitanConfig, TraderProfile, load_config
from titan_tfbs.execution.orders import ExitReason
from titan_tfbs.instruments import FIRM_PRIMARY_SYMBOLS, get_instrument
from titan_tfbs.journal.journal import TradeJournal
from titan_tfbs.live import SYNTHETIC, load_candles


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="reports/results.json")
    ap.add_argument("--config", default="config/titan.yaml")
    ap.add_argument("--symbols", default=",".join(FIRM_PRIMARY_SYMBOLS))
    ap.add_argument("--data", help="directory of SYMBOL.csv 5M bars")
    ap.add_argument("--journal-dir", default="reports/journal")
    ap.add_argument("--profile", choices=[p.value for p in TraderProfile])
    ap.add_argument("--balance", type=float)
    args = ap.parse_args(argv)

    cfg = load_config(args.config) if Path(args.config).exists() else TitanConfig()
    cfg.journal.directory = args.journal_dir
    if args.profile:
        cfg.account.profile = TraderProfile(args.profile)
    if args.balance:
        cfg.account.starting_balance = args.balance

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    synthetic = not args.data
    if synthetic:
        cfg.mtf.min_bias_bars = 20

    candles = load_candles(symbols, args.data)
    if not candles:
        raise SystemExit("no candles to replay")

    bot = TFBSBot(
        cfg,
        symbols=list(candles),
        start_time=min(c[0].ts for c in candles.values()),
        journal=TradeJournal(cfg.journal),
    )

    stream = sorted(
        ((c.ts, s, c) for s, cs in candles.items() for c in cs),
        key=lambda row: (row[0], row[1]),
    )
    print(f"replaying {len(stream):,} bars across {len(candles)} instruments...")

    equity_curve: List = []
    peak_open_risk = 0.0
    evaluated = accepted = 0
    # Track when the account left ACTIVE. A run that ends healthy can still
    # have been halted mid-way, and that is exactly what a risk review needs
    # to see.
    status_timeline: List[dict] = []
    halted_bars = 0
    last_status = "ACTIVE"

    for i, (ts, symbol, candle) in enumerate(stream, 1):
        evals = bot.on_candle(symbol, candle)
        evaluated += len(evals)
        accepted += sum(1 for e in evals if e.accepted)
        peak_open_risk = max(peak_open_risk, bot.risk.aggregate_open_risk_pct)

        verdict = bot.risk.limits()
        if not verdict.can_trade:
            halted_bars += 1
        if verdict.status.value != last_status:
            status_timeline.append({
                "ts": ts.isoformat(),
                "from": last_status,
                "to": verdict.status.value,
                "can_trade": verdict.can_trade,
                "daily_dd_pct": round(verdict.daily_dd_pct, 3),
                "max_dd_pct": round(verdict.max_dd_pct, 3),
                "breaches": list(verdict.breaches),
            })
            last_status = verdict.status.value

        if i % 288 == 0:
            equity_curve.append((ts, bot.risk.state.equity))
            bot.log_equity_point()
        if i % 20000 == 0:
            print(f"  {i:,}/{len(stream):,} bars")

    bot.close_all(ExitReason.END_OF_DATA)
    end_ts = stream[-1][0]
    equity_curve.append((end_ts, bot.risk.state.equity))

    metrics = compute_metrics(
        bot.closed_positions, equity_curve, cfg.account.starting_balance
    )

    # Per-instrument breakdown, so the report can show where the edge sat.
    by_symbol: Dict[str, dict] = {}
    for symbol in candles:
        rows = [p for p in bot.closed_positions if p.signal.symbol == symbol]
        inst = get_instrument(symbol)
        wins = sum(1 for p in rows if p.realized_pnl > 0)
        gross_p = sum(p.realized_pnl for p in rows if p.realized_pnl > 0)
        gross_l = -sum(p.realized_pnl for p in rows if p.realized_pnl < 0)
        by_symbol[symbol] = {
            "asset_class": inst.asset_class.value,
            "description": inst.description,
            "bars": len(candles[symbol]),
            "trades": len(rows),
            "wins": wins,
            "losses": sum(1 for p in rows if p.realized_pnl < 0),
            "win_rate": (wins / len(rows) * 100.0) if rows else 0.0,
            "pnl": sum(p.realized_pnl for p in rows),
            "avg_r": (sum(p.realized_r for p in rows) / len(rows)) if rows else 0.0,
            "profit_factor": (gross_p / gross_l) if gross_l > 0 else (
                float("inf") if gross_p > 0 else 0.0
            ),
            "screens": {
                "trend": "/".join(inst.timeframes.trend),
                "pattern": "/".join(inst.timeframes.pattern),
                "entry": "/".join(inst.timeframes.entry),
            },
        }

    trades = [
        {
            **p.summary(),
            "pattern_tf": p.signal.pattern_timeframe,
            "entry_method": p.signal.entry_method.value,
            "risk_pct": p.signal.risk_pct,
            "rr_planned": p.signal.risk_reward,
            "tp1": p.signal.take_profit_1,
            "tp2": p.signal.take_profit_2,
            "score_breakdown": p.signal.score.breakdown(),
        }
        for p in bot.closed_positions
    ]

    # Why setups were turned away — the Ch XII-A4 "skipped" record, aggregated.
    rejections: Dict[str, int] = {}
    for event in bot.events:
        if event.kind == "rejected":
            key = _bucket(event.detail)
            rejections[key] = rejections.get(key, 0) + 1

    payload = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "data_source": "synthetic" if synthetic else str(args.data),
        "synthetic": synthetic,
        "config": cfg.to_dict(),
        "period": {"start": stream[0][0].isoformat(), "end": end_ts.isoformat()},
        "bars": len(stream),
        "symbols": list(candles),
        "setups_evaluated": evaluated,
        "setups_executed": accepted,
        "peak_open_risk_pct": peak_open_risk,
        "status_timeline": status_timeline,
        "halted_bars": halted_bars,
        "halted_pct": round(halted_bars / max(1, len(stream)) * 100.0, 2),
        "metrics": metrics.to_dict(),
        "by_symbol": by_symbol,
        "trades": trades,
        "rejections": dict(sorted(rejections.items(), key=lambda kv: -kv[1])),
        "equity_curve": [(ts.isoformat(), round(eq, 2)) for ts, eq in equity_curve],
        "account": bot.risk.report(end_ts),
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, default=str))
    print(
        f"\n{metrics.trades} trades, {metrics.win_rate:.1f}% win rate, "
        f"PF {metrics.profit_factor:.2f}, max DD {metrics.max_drawdown_pct:.2f}%"
    )
    print(f"peak aggregate open risk {peak_open_risk:.2f}% "
          f"(Ch VIII-A cap {cfg.risk.max_aggregate_open_risk_pct:.0f}%)")
    print(f"-> {out}")
    return 0


def _bucket(detail: str) -> str:
    """Collapse rejection messages into reportable categories."""
    d = detail.lower()
    for needle, label in (
        ("confluence", "Confluence score below 7/10 (Ch XI)"),
        ("below the firm minimum", "R:R below 2:1 (Ch VIII-A)"),
        ("counter-trend", "Counter-trend to HTF bias (Ch IX)"),
        ("checklist", "Pre-trade checklist blocked (Appendix A)"),
        ("correlated", "Correlated exposure cap (Ch VIII-A)"),
        ("aggregate", "Aggregate open risk cap (Ch VIII-A)"),
        ("already holding", "One position per instrument (Ch VIII-C)"),
        ("trades already taken", "Trade cadence cap (Ch XIII-B)"),
        ("invalidated", "Formation invalidated before entry (Ch V-B)"),
        ("expired", "Formation expired unbroken (Ch VI-B)"),
        ("stop distance", "Stop too wide for a 2R target"),
        ("account", "Account halted by drawdown limits"),
    ):
        if needle in d:
            return label
    return "Other"


if __name__ == "__main__":
    raise SystemExit(main())
