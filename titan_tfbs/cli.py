"""Command-line interface.

    python -m titan_tfbs demo
    python -m titan_tfbs backtest --data ./data --symbols XAUUSD,NQ
    python -m titan_tfbs scan --data ./data --symbols XAUUSD
    python -m titan_tfbs instruments
    python -m titan_tfbs checklist
    python -m titan_tfbs config --config config/titan.yaml
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Sequence

from titan_tfbs import __firm__, __version__
from titan_tfbs.backtest.engine import BacktestEngine
from titan_tfbs.bot import TFBSBot
from titan_tfbs.config import TitanConfig, TraderProfile, load_config
from titan_tfbs.data.feed import CSVFeed, InMemoryFeed
from titan_tfbs.data.news import EconomicCalendar
from titan_tfbs.instruments import CATALOG, FIRM_PRIMARY_SYMBOLS, get_instrument
from titan_tfbs.journal.journal import TradeJournal

BANNER = f"TITAN MARKETS LLC — Titan Formation Breakout System v{__version__}"


def main(argv: Optional[Sequence[str]] = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)

    # `live` owns its own flag set, so hand it the rest of the command line
    # untouched rather than teaching this parser about all of them.
    if raw and raw[0] == "live":
        from titan_tfbs.live import main as live_main

        return live_main(raw[1:], prog="titan-tfbs live")

    parser = _build_parser()
    args = parser.parse_args(raw)
    if not getattr(args, "command", None):
        parser.print_help()
        return 1
    return args.handler(args)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="titan_tfbs",
        description=f"{BANNER}\n{__firm__} — proprietary strategy engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=BANNER)
    sub = parser.add_subparsers(dest="command")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", help="path to a YAML or JSON config file")
    common.add_argument(
        "--symbols", help="comma-separated symbols (default: the firm's primaries)"
    )
    common.add_argument(
        "--profile",
        choices=[p.value for p in TraderProfile],
        help="rule set: prop_desk (1-2%% risk) or titan_entry (0.5%%, Method C)",
    )
    common.add_argument("--balance", type=float, help="starting account balance")
    common.add_argument("--journal-dir", help="directory for the Ch XIII journal")
    common.add_argument("--calendar", help="economic calendar CSV/JSON for Ch XII-A6")
    common.add_argument(
        "--no-journal", action="store_true", help="disable journal writing"
    )

    p_backtest = sub.add_parser(
        "backtest", parents=[common], help="replay history through the full pipeline"
    )
    p_backtest.add_argument("--data", required=True, help="directory of SYMBOL.csv files")
    p_backtest.add_argument(
        "--warmup", type=int, default=2000, help="base bars used to build the screens"
    )
    p_backtest.add_argument("--json", action="store_true", help="emit JSON")
    p_backtest.set_defaults(handler=_cmd_backtest)

    p_scan = sub.add_parser(
        "scan", parents=[common], help="scan history and report the current watchlist"
    )
    p_scan.add_argument("--data", required=True, help="directory of SYMBOL.csv files")
    p_scan.add_argument("--json", action="store_true", help="emit JSON")
    p_scan.set_defaults(handler=_cmd_scan)

    p_demo = sub.add_parser(
        "demo", parents=[common], help="run the engine on deterministic synthetic data"
    )
    p_demo.add_argument(
        "--warmup", type=int, default=5000, help="base bars used to build the screens"
    )
    p_demo.add_argument("--json", action="store_true", help="emit JSON")
    p_demo.set_defaults(handler=_cmd_demo)

    # Listed for the help text only — `live` is intercepted in main() and its
    # arguments are passed straight through to the live runner.
    sub.add_parser("live", add_help=False,
         help="run the bot and watch it trade (`live --help` for its flags)")

    p_inst = sub.add_parser("instruments", help="list the firm's tradeable universe")
    p_inst.add_argument("--asset-class", help="filter by asset class")
    p_inst.set_defaults(handler=_cmd_instruments)

    p_check = sub.add_parser("checklist", help="print the Appendix A pre-trade checklist")
    p_check.set_defaults(handler=_cmd_checklist)

    p_cfg = sub.add_parser("config", parents=[common], help="dump the effective config")
    p_cfg.set_defaults(handler=_cmd_config)

    return parser


# --------------------------------------------------------------------------
# config assembly
# --------------------------------------------------------------------------


def _config_from_args(args) -> TitanConfig:
    cfg = load_config(getattr(args, "config", None))
    if getattr(args, "profile", None):
        cfg.account.profile = TraderProfile(args.profile)
    if getattr(args, "balance", None):
        cfg.account.starting_balance = args.balance
    if getattr(args, "symbols", None):
        cfg.symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if getattr(args, "journal_dir", None):
        cfg.journal.directory = args.journal_dir
    if getattr(args, "no_journal", False):
        cfg.journal.enabled = False
    return cfg


def _calendar_from_args(args) -> Optional[EconomicCalendar]:
    path = getattr(args, "calendar", None)
    return EconomicCalendar.from_file(path) if path else None


def _symbols(cfg: TitanConfig) -> List[str]:
    return cfg.symbols or list(FIRM_PRIMARY_SYMBOLS)


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------


def _cmd_backtest(args) -> int:
    cfg = _config_from_args(args)
    feed = CSVFeed(args.data, symbols=_symbols(cfg) if cfg.symbols else None)
    engine = BacktestEngine(
        cfg,
        symbols=_symbols(cfg),
        journal=TradeJournal(cfg.journal),
        calendar=_calendar_from_args(args),
        warmup_bars=args.warmup,
    )
    result = engine.run(feed)
    if args.json:
        print(json.dumps(result.summary(), indent=2, default=str))
    else:
        print(BANNER)
        print(result.render())
        print(f"\nJournal: {cfg.journal.directory}/")
    return 0


def _cmd_scan(args) -> int:
    """Research mode: steps 1-4 of the Ch VI-A pipeline, no orders.

    Scanning reports formations and scored setups; it deliberately stops
    before SIZE and EXECUTE, so the account-level gates (aggregate risk, the
    drawdown ladder, the Appendix A checklist) are not applied here. Use
    ``backtest`` or the live bot for anything that commits capital.
    """
    from titan_tfbs.core.candles import MultiTimeframeStore, TimeFrame
    from titan_tfbs.instruments import get_instrument
    from titan_tfbs.strategy.tfbs import TFBSStrategy

    cfg = _config_from_args(args)
    feed = CSVFeed(args.data, symbols=_symbols(cfg) if cfg.symbols else None)
    universe = _symbols(cfg) or feed.symbols()

    strategy = TFBSStrategy(cfg)
    setups: List[dict] = []
    watchlists: dict = {}

    for symbol in universe:
        candles = list(feed.history(symbol))
        if not candles:
            continue
        instrument = get_instrument(symbol)
        store = MultiTimeframeStore(
            symbol, TimeFrame.parse("5M"), instrument.timeframes.all_timeframes()
        )
        for candle in candles:
            closed = store.push(candle)
            for evaluation in strategy.on_candles(symbol, store, closed, candle.ts):
                if evaluation.accepted:
                    setups.append(evaluation.summary())
        watchlists[symbol] = strategy.watchlist(symbol)

    if args.json:
        print(json.dumps({"setups": setups, "watchlists": watchlists}, indent=2, default=str))
        return 0

    print(BANNER)
    print(f"\nScanned {len(universe)} instrument(s): {', '.join(universe)}")
    for symbol, watch in watchlists.items():
        if not watch:
            continue
        print(f"\n{symbol} — Ch VI-B watchlist ({len(watch)} formation(s)):")
        for item in watch:
            print(
                f"  {item['type']:<8} {item['timeframe']:<4} "
                f"{item['direction']:<5} trigger {item['trigger_price']:<12.5f} "
                f"quality {item['quality_points']}/2  state={item['watch_state']}"
            )
    print(f"\nSetups that passed SCAN -> VALIDATE -> CONFIRM -> SCORE: {len(setups)}")
    for s in setups:
        sig = s["signal"]
        print(
            f"  {s['ts'][:16]}  {s['symbol']:<8} {sig['pattern']:<8} "
            f"{sig['direction']:<5} {s['grade']:<9} {s['score']}/10  "
            f"entry {sig['entry']:<12.5f} SL {sig['stop_loss']:<12.5f} "
            f"TP1 {sig['tp1']:<12.5f} R:R {sig['rr']:.2f}"
        )
    if setups:
        print(
            "\nThese have not been sized or checklisted — run `backtest` or the "
            "live bot to apply the Ch VIII risk gate and Appendix A."
        )
    return 0


def _cmd_demo(args) -> int:
    from titan_tfbs.data.synthetic import firm_scenario

    cfg = _config_from_args(args)
    start = datetime(2026, 1, 5, tzinfo=timezone.utc)
    data = {
        "XAUUSD": firm_scenario(start, 2650.0, height_pct=0.022, seed=17),
        "EURUSD": firm_scenario(start, 1.0850, height_pct=0.013, seed=29),
        "NQ": firm_scenario(start, 21500.0, height_pct=0.018, seed=41),
    }
    feed = InMemoryFeed(data)

    engine = BacktestEngine(
        cfg,
        symbols=list(data),
        journal=TradeJournal(cfg.journal),
        # Build the Ch IX Screen 1 daily bias before any trade is allowed.
        warmup_bars=args.warmup,
    )
    result = engine.run(feed, equity_sample_bars=288)

    if args.json:
        print(json.dumps(result.summary(), indent=2, default=str))
        return 0

    print(BANNER)
    print(
        "\nDeterministic synthetic data: H&S, Inverse H&S, Double Top/Bottom and\n"
        "Triple formations interleaved with dead ranges, on three asset classes."
    )
    print(result.render())
    print("\nEvent log (entries, exits and limit events):")
    for event in result.events:
        if event.kind in ("entry", "exit", "limit") and "new session" not in event.detail:
            print(f"  {event}")
    return 0


def _cmd_instruments(args) -> int:
    print(BANNER)
    print("\nTFBS Ch I — Applicable Markets\n")
    header = (
        f"{'SYMBOL':<8} {'CLASS':<12} {'TICK':>8} {'TICK VALUE':>12} "
        f"{'SCREENS (trend > pattern > entry)':<32} DESCRIPTION"
    )
    print(header)
    print("-" * len(header))
    for symbol, inst in sorted(CATALOG.items()):
        if args.asset_class and inst.asset_class.value != args.asset_class:
            continue
        screens = (
            f"{'/'.join(inst.timeframes.trend)} > "
            f"{'/'.join(inst.timeframes.pattern)} > "
            f"{'/'.join(inst.timeframes.entry)}"
        )
        # Tick value is quoted in the instrument's own quote currency; the
        # sizing layer converts it into the account currency.
        tick_value = f"{inst.tick_value:.2f} {inst.quote_currency}"
        print(
            f"{symbol:<8} {inst.asset_class.value:<12} {inst.tick_size:>8g} "
            f"{tick_value:>12} {screens:<32} {inst.description}"
        )
    print(f"\nPrimary scan universe: {', '.join(FIRM_PRIMARY_SYMBOLS)}")
    return 0


def _cmd_checklist(args) -> int:
    print(BANNER)
    print(
        """
APPENDIX A — TFBS PRE-TRADE CHECKLIST        [M] Mandatory  [P] Preferred

PATTERN IDENTIFICATION
  [M] Is a H&S, DT/DB, or Triple Top/Bottom clearly visible?
  [M] Does the pattern follow an established prior trend?
  [M] Do quality filters pass? (Symmetry, spacing, volume profile)

BREAKOUT CONFIRMATION
  [M] Has the neckline / confirmation line been broken by a CANDLE CLOSE?
  [P] Is breakout volume > 1.5x the 20-period average?
  [P] Has a retest occurred? (Required for Method B/C entries)

RISK & REWARD
  [M] Is stop-loss at the structural invalidation point?
  [M] Is risk limited to 1-2% of account (0.5% for Titan Entry)?
  [M] Does measured move provide minimum 2:1 R:R?
  [P] Is path to TP1 clear of major S/R obstacles?

TIMEFRAME ALIGNMENT
  [M] Is Screen 1 (Daily/Weekly) aligned with trade direction?
  [M] Is Screen 2 (4H/1H) showing confirmed formation?
  [M] Is Screen 3 (15M/5M) providing precision entry?

FINAL AUTHORIZATION
  [M] Confluence Score = 7/10 or higher?
  [M] No high-impact news within 30 minutes?
  [M] Aggregate portfolio risk under 5%?
  [M] Trade logged in journal with annotated screenshot?

Every item is enforced in code: see titan_tfbs/strategy/checklist.py.
"""
    )
    return 0


def _cmd_config(args) -> int:
    cfg = _config_from_args(args)
    print(json.dumps(cfg.to_dict(), indent=2, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
