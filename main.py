#!/usr/bin/env python3
"""Titan Formation Breakout System — main entry point.

    python main.py                          run the bot on synthetic data
    python main.py --data ./data            run it on your own 5M CSVs
    python main.py --symbols XAUUSD,NQ      pick the universe
    python main.py --profile titan_entry    trade the Ch XII-B rule set

    python main.py backtest --data ./data   replay history, full risk gate
    python main.py scan     --data ./data   formations and scored setups only
    python main.py demo                     deterministic end-to-end run
    python main.py instruments              the Ch I tradeable universe
    python main.py checklist                the Appendix A pre-trade checklist
    python main.py config                   the effective configuration

Runs straight from a clone — no install required. `pip install -e .` also gives
you the same thing as the `titan-tfbs` command.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make the package importable when run from a bare clone, before any
# titan_tfbs import is attempted.
sys.path.insert(0, str(Path(__file__).resolve().parent))

MIN_PYTHON = (3, 10)

#: Subcommands handled by titan_tfbs.cli. Anything else is treated as flags
#: for the live runner, so `python main.py --data ./data` just works.
CLI_COMMANDS = {
    "backtest", "scan", "demo", "instruments", "checklist", "config",
}
LIVE_COMMANDS = {"live", "run", "bot"}

BANNER = "TITAN MARKETS LLC — Titan Formation Breakout System"


def _check_python() -> None:
    if sys.version_info < MIN_PYTHON:
        need = ".".join(str(p) for p in MIN_PYTHON)
        have = ".".join(str(p) for p in sys.version_info[:3])
        raise SystemExit(f"{BANNER}\nPython {need}+ is required; this is {have}.")


def _usage() -> str:
    return f"""{BANNER}

USAGE
  python main.py [live options]        run the bot and watch it trade (default)
  python main.py <command> [options]   run a specific command

COMMANDS
  live | run          stream candles through the bot, printing every fill
  backtest            replay history through the full pipeline and risk gate
  scan                report formations and scored setups; places no orders
  demo                deterministic end-to-end run on synthetic data
  instruments         the Ch I tradeable universe with contract specs
  checklist           the Appendix A pre-trade checklist
  config              dump the effective configuration as JSON

EXAMPLES
  python main.py
  python main.py --symbols XAUUSD,EURUSD,NQ
  python main.py --data ./data --journal-dir ./journal
  python main.py backtest --data ./data --config config/titan.yaml
  python main.py scan --data ./data --symbols XAUUSD

  Any command's own flags:  python main.py backtest --help

NOTE
  With no --data, deterministic synthetic formations are used so a first run
  works with nothing to download. Those results demonstrate mechanics only —
  judge the strategy on firm data.
"""


def main(argv: list[str] | None = None) -> int:
    _check_python()
    args = list(sys.argv[1:] if argv is None else argv)

    if args and args[0] in ("-h", "--help", "help"):
        print(_usage())
        return 0

    try:
        if args and args[0] in CLI_COMMANDS:
            from titan_tfbs.cli import main as cli_main
            return cli_main(args)

        # Default action, and the `live`/`run`/`bot` aliases: run the bot.
        from titan_tfbs.live import main as live_main
        if args and args[0] in LIVE_COMMANDS:
            args = args[1:]
        return live_main(args, prog="python main.py")

    except KeyboardInterrupt:
        print("\ninterrupted — no orders left pending", file=sys.stderr)
        return 130
    except FileNotFoundError as exc:
        print(f"{exc}", file=sys.stderr)
        return 2
    except ModuleNotFoundError as exc:
        # PyYAML is the only optional dependency, and only for YAML configs.
        if exc.name == "yaml":
            print(
                "PyYAML is needed to read a YAML config.\n"
                "  pip install pyyaml        (or use a .json config, or omit "
                "--config for firm defaults)",
                file=sys.stderr,
            )
            return 2
        raise


if __name__ == "__main__":
    raise SystemExit(main())
