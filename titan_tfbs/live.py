"""Live session runner — drive the bot bar by bar and report as it goes.

This is the closest thing to watching the desk. It builds the bot exactly as
production would (same config, same risk gate, same trade management), feeds it
candles one at a time, prints entries, exits and limit events as they happen,
and reports the account state at the close.

Used by ``main.py``, by ``titan-tfbs live`` and by ``scripts/live_session.py``,
so there is one implementation rather than three.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from titan_tfbs.bot import BotEvent, TFBSBot
from titan_tfbs.config import (
    MissingYAMLSupport,
    TitanConfig,
    TraderProfile,
    load_config,
)
from titan_tfbs.core.candles import Candle
from titan_tfbs.data.feed import CSVFeed
from titan_tfbs.data.news import EconomicCalendar
from titan_tfbs.data.synthetic import COMPACT_PLAN, firm_scenario
from titan_tfbs.journal.journal import TradeJournal

#: Price anchors and seeds for the synthetic fallback, per instrument. Used
#: only when no --data directory is supplied.
SYNTHETIC: Dict[str, tuple] = {
    "XAUUSD": (2650.0, 0.022, 17),
    "EURUSD": (1.0850, 0.013, 29),
    "GBPUSD": (1.2700, 0.013, 31),
    "USDJPY": (152.00, 0.014, 37),
    "NQ": (21500.0, 0.018, 41),
    "ES": (5900.0, 0.015, 53),
    "RTY": (2300.0, 0.020, 59),
    "CL": (72.50, 0.030, 67),
}


def load_candles(
    symbols: Sequence[str],
    data_dir: Optional[str] = None,
    start: Optional[datetime] = None,
) -> Dict[str, List[Candle]]:
    """Load 5M bars per symbol, from CSV if given, else synthetic."""
    if data_dir:
        feed = CSVFeed(data_dir, symbols=list(symbols))
        loaded = {s: list(feed.history(s)) for s in symbols}
        return {s: c for s, c in loaded.items() if c}

    begin = start or datetime(2026, 1, 5, tzinfo=timezone.utc)
    out: Dict[str, List[Candle]] = {}
    for symbol in symbols:
        key = symbol.upper()
        if key not in SYNTHETIC:
            raise SystemExit(
                f"no synthetic profile for {key} — pass --data with a "
                f"{key}.csv of 5M bars, or pick one of: {', '.join(SYNTHETIC)}"
            )
        price, height, seed = SYNTHETIC[key]
        out[key] = firm_scenario(begin, price, height, seed=seed, plan=COMPACT_PLAN)
    return out


def run_session(
    config: TitanConfig,
    candles: Dict[str, List[Candle]],
    calendar: Optional[EconomicCalendar] = None,
    quiet: bool = False,
    source_label: str = "",
) -> TFBSBot:
    """Replay ``candles`` through a live bot, printing as it trades."""
    if not candles:
        raise SystemExit("no candles to replay")

    def on_event(event: BotEvent) -> None:
        if quiet:
            return
        # Session rollovers are noise; everything else is worth seeing.
        interesting = event.kind in ("entry", "exit", "compliance") or (
            event.kind == "limit" and "new session" not in event.detail
        )
        if interesting:
            print(f"  {event}", flush=True)

    bot = TFBSBot(
        config,
        symbols=list(candles),
        start_time=min(c[0].ts for c in candles.values()),
        journal=TradeJournal(config.journal),
        calendar=calendar,
        on_event=on_event,
    )

    print("TITAN MARKETS LLC — TFBS bot online")
    print(
        f"  account   {config.account.name}  "
        f"${config.account.starting_balance:,.0f}  "
        f"profile={config.account.profile.value}"
    )
    print(f"  universe  {', '.join(candles)}")
    print(f"  entry     Method {config.required_entry_method().value} (Ch VII)")
    print(
        f"  screens   {'/'.join(config.mtf.trend_timeframes)} > "
        f"{'/'.join(config.mtf.pattern_timeframes)} > "
        f"{'/'.join(config.mtf.entry_timeframes)}"
    )
    print(f"  source    {source_label or 'deterministic synthetic data'}")
    print(f"\nstreaming {sum(len(c) for c in candles.values()):,} 5M candles...\n")

    # Interleave symbols in strict timestamp order: the Ch VIII-A portfolio
    # limits only bind correctly if symbols compete for the same headroom in
    # the order the market delivered them.
    stream = sorted(
        ((c.ts, s, c) for s, cs in candles.items() for c in cs),
        key=lambda row: (row[0], row[1]),
    )
    for _, symbol, candle in stream:
        bot.on_candle(symbol, candle)

    bot.close_all()
    report(bot, config)
    return bot


def report(bot: TFBSBot, config: TitanConfig) -> None:
    """Print the closing account state."""
    snap = bot.snapshot()
    acct = snap["account"]
    limits = acct["limits"]
    print(f"\nsession closed at {snap['time'][:16]}")
    print(
        f"  balance    ${acct['balance']:,.2f}   "
        f"(from ${config.account.starting_balance:,.0f})"
    )
    print(f"  status     {limits['status']}   can_trade={limits['can_trade']}")
    print(
        f"  daily DD   {limits['daily_dd_pct']:.2f}%   "
        f"max DD {limits['max_dd_pct']:.2f}%"
    )
    print(
        f"  open risk  {acct['aggregate_open_risk_pct']:.2f}% "
        f"(cap {config.risk.max_aggregate_open_risk_pct:.0f}%)"
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
    print(f"\n  journal -> {config.journal.directory}/")


def build_parser(prog: Optional[str] = None) -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog=prog, description="Run the TFBS bot and watch it trade."
    )
    ap.add_argument("--config", default=None,
                    help="YAML or JSON config (default: config/titan.yaml)")
    ap.add_argument("--symbols", default="XAUUSD,NQ",
                    help="comma-separated symbols (default: XAUUSD,NQ)")
    ap.add_argument("--data", help="directory of SYMBOL.csv 5M bars; omit for synthetic")
    ap.add_argument("--calendar", help="economic calendar CSV/JSON (Ch XII-A6)")
    ap.add_argument("--journal-dir", default="journal",
                    help="where the Ch XIII journal is written (default: journal)")
    ap.add_argument("--balance", type=float, help="starting account balance")
    ap.add_argument("--profile", choices=[p.value for p in TraderProfile],
                    help="prop_desk (1-2%%) or titan_entry (0.5%%, Method C)")
    ap.add_argument("--quiet", action="store_true", help="summary only, no event stream")
    return ap


DEFAULT_CONFIG = "config/titan.yaml"


def _resolve_config(path: Optional[str]) -> TitanConfig:
    """Load the config, degrading gracefully only where that is safe.

    If the user named a config explicitly, its contents are what they intend to
    trade — failing to read it must be an error, never a silent substitution of
    different risk parameters. Only the shipped default is allowed to fall back
    to the built-in firm defaults, which it matches value for value.
    """
    explicit = path is not None
    target = Path(path or DEFAULT_CONFIG)

    if not target.exists():
        if explicit:
            raise SystemExit(f"config file not found: {target}")
        return load_config()

    try:
        return load_config(target)
    except MissingYAMLSupport as exc:
        if explicit:
            raise SystemExit(str(exc)) from exc
        print(
            f"warning: {exc}\n"
            f"         falling back to the built-in firm defaults, which match "
            f"the shipped {DEFAULT_CONFIG}.\n"
            f"         If you have edited that file, install PyYAML before "
            f"trading — your changes are NOT being applied.",
            file=sys.stderr,
        )
        return load_config()


def main(argv: Optional[Sequence[str]] = None, prog: Optional[str] = None) -> int:
    args = build_parser(prog).parse_args(argv)

    cfg = _resolve_config(args.config)
    cfg.journal.directory = args.journal_dir
    if args.balance:
        cfg.account.starting_balance = args.balance
    if args.profile:
        cfg.account.profile = TraderProfile(args.profile)

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if not args.data:
        # Synthetic series are far shorter than production history, so the
        # Ch IX Screen 1 daily bias needs a correspondingly lower bar count
        # before it is usable at all.
        cfg.mtf.min_bias_bars = 20

    candles = load_candles(symbols, args.data)
    calendar = EconomicCalendar.from_file(args.calendar) if args.calendar else None

    run_session(cfg, candles, calendar, quiet=args.quiet, source_label=args.data or "")
    return 0
