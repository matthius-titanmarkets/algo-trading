#!/usr/bin/env python3
"""Drive the TFBS bot as it runs live: stream 5M candles, print events.

This is the closest thing to watching the desk. It builds the bot exactly as
production would — same config, same risk gate, same trade management — feeds
it bars one at a time, and prints entries, exits and limit events as they
happen, then reports the account state at the close.

    python scripts/live_session.py                       # synthetic data
    python scripts/live_session.py --data ./data         # your own CSVs
    python scripts/live_session.py --symbols XAUUSD,NQ --profile titan_entry

With ``--data`` it replays real CSV history. Without it, deterministic
synthetic formations are used so the run is reproducible.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from titan_tfbs import load_config
from titan_tfbs.bot import TFBSBot
from titan_tfbs.config import TraderProfile
from titan_tfbs.data.feed import CSVFeed
from titan_tfbs.data.news import EconomicCalendar
from titan_tfbs.data.synthetic import COMPACT_PLAN, firm_scenario
from titan_tfbs.journal.journal import TradeJournal

#: Seeds and price anchors for the synthetic fallback, per instrument.
SYNTHETIC = {
    "XAUUSD": (2650.0, 0.022, 17),
    "EURUSD": (1.0850, 0.013, 29),
    "GBPUSD": (1.2700, 0.013, 31),
    "NQ": (21500.0, 0.018, 41),
    "ES": (5900.0, 0.015, 53),
    "CL": (72.50, 0.030, 67),
}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", default="config/titan.yaml")
    ap.add_argument("--symbols", default="XAUUSD,NQ")
    ap.add_argument("--data", help="directory of SYMBOL.csv 5M bars")
    ap.add_argument("--calendar", help="economic calendar CSV/JSON (Ch XII-A6)")
    ap.add_argument("--journal-dir", default="journal")
    ap.add_argument("--balance", type=float)
    ap.add_argument("--profile", choices=[p.value for p in TraderProfile])
    ap.add_argument(
        "--quiet", action="store_true", help="summary only, no event stream"
    )
    args = ap.parse_args(argv)

    cfg = load_config(args.config) if Path(args.config).exists() else load_config()
    cfg.journal.directory = args.journal_dir
    if args.balance:
        cfg.account.starting_balance = args.balance
    if args.profile:
        cfg.account.profile = TraderProfile(args.profile)

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    if args.data:
        feed = CSVFeed(args.data, symbols=symbols)
        data = {s: list(feed.history(s)) for s in symbols}
        data = {s: c for s, c in data.items() if c}
    else:
        # Synthetic runs need less daily history than production before the
        # Ch IX Screen 1 bias is usable.
        cfg.mtf.min_bias_bars = 20
        start = datetime(2026, 1, 5, tzinfo=timezone.utc)
        data = {}
        for symbol in symbols:
            if symbol not in SYNTHETIC:
                raise SystemExit(f"no synthetic profile for {symbol}; pass --data")
            price, height, seed = SYNTHETIC[symbol]
            data[symbol] = firm_scenario(
                start, price, height, seed=seed, plan=COMPACT_PLAN
            )
    if not data:
        raise SystemExit("no candles to replay")

    calendar = EconomicCalendar.from_file(args.calendar) if args.calendar else None

    def on_event(ev):
        if args.quiet:
            return
        interesting = ev.kind in ("entry", "exit", "compliance") or (
            ev.kind == "limit" and "new session" not in ev.detail
        )
        if interesting:
            print(f"  {ev}", flush=True)

    bot = TFBSBot(
        cfg,
        symbols=list(data),
        start_time=min(c[0].ts for c in data.values()),
        journal=TradeJournal(cfg.journal),
        calendar=calendar,
        on_event=on_event,
    )

    print("TITAN MARKETS LLC — TFBS bot online")
    print(
        f"  account   {cfg.account.name}  "
        f"${cfg.account.starting_balance:,.0f}  profile={cfg.account.profile.value}"
    )
    print(f"  universe  {', '.join(data)}")
    print(f"  entry     Method {cfg.required_entry_method().value} (Ch VII)")
    print(
        f"  screens   {'/'.join(cfg.mtf.trend_timeframes)} > "
        f"{'/'.join(cfg.mtf.pattern_timeframes)} > "
        f"{'/'.join(cfg.mtf.entry_timeframes)}"
    )
    print(f"  source    {args.data or 'deterministic synthetic data'}")
    print(f"\nstreaming {sum(len(c) for c in data.values()):,} 5M candles...\n")

    stream = sorted(
        ((c.ts, s, c) for s, cs in data.items() for c in cs),
        key=lambda row: (row[0], row[1]),
    )
    for _, symbol, candle in stream:
        bot.on_candle(symbol, candle)

    bot.close_all()
    _report(bot, cfg)
    return 0


def _report(bot: TFBSBot, cfg) -> None:
    snap = bot.snapshot()
    acct = snap["account"]
    limits = acct["limits"]
    print(f"\nsession closed at {snap['time'][:16]}")
    print(
        f"  balance    ${acct['balance']:,.2f}   "
        f"(from ${cfg.account.starting_balance:,.0f})"
    )
    print(f"  status     {limits['status']}   can_trade={limits['can_trade']}")
    print(
        f"  daily DD   {limits['daily_dd_pct']:.2f}%   "
        f"max DD {limits['max_dd_pct']:.2f}%"
    )
    print(
        f"  open risk  {acct['aggregate_open_risk_pct']:.2f}% "
        f"(cap {cfg.risk.max_aggregate_open_risk_pct:.0f}%)"
    )
    print(f"  compliance {acct['compliance']['flag']}")
    print(f"  trades     {snap['closed_trades']} closed")
    for breach in limits["breaches"]:
        print(f"  ! {breach}")
    for symbol, watch in snap["watchlists"].items():
        if watch:
            summary = ", ".join(
                f"{w['type']}@{w['timeframe']}({w['watch_state']})" for w in watch
            )
            print(f"  {symbol} watchlist: {summary}")
    print(f"\n  journal -> {cfg.journal.directory}/")


if __name__ == "__main__":
    raise SystemExit(main())
