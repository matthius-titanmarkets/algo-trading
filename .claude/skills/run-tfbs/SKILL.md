---
name: run-tfbs
description: Launch and drive the Titan Formation Breakout System trading bot. Use when asked to run, open, start, or demo the bot, watch it trade, scan for setups, backtest against data, or verify the engine works end to end.
---

# Running the TFBS bot

Pure-stdlib engine. Python >= 3.10. No numpy, no pandas, no network access.
PyYAML is only needed to read `config/titan.yaml` (JSON configs work without it).

## Setup

No install is needed — `main.py` puts the repo on `sys.path` itself:

```bash
python main.py --help
```

To get the `titan-tfbs` command on PATH as well:

```bash
pip install -e .
titan-tfbs --version
# → TITAN MARKETS LLC — Titan Formation Breakout System v1.0.0
```

## Open the bot — a live session

This is the one to reach for when asked to "open" or "watch" the bot. It
streams 5M candles through the real `TFBSBot` and prints entries, exits and
limit events as they happen, then reports account state at the close.

```bash
python main.py --symbols XAUUSD,NQ --journal-dir ./journal
```

Equivalent: `titan-tfbs live ...` and `python scripts/live_session.py ...` —
all three call `titan_tfbs.live`.

Expected shape (deterministic — same input, same output every run):

```
TITAN MARKETS LLC — TFBS bot online
  account   TITAN-DESK-01  $500,000  profile=prop_desk
  entry     Method B (Ch VII)
  screens   1D/1W > 4H/1H > 15M/5M

streaming 8,460 5M candles...

  [2026-01-09 06:15] XAUUSD entry: APPROVED H&S short 30.41 @ 2693.40000 ...
  [2026-01-09 16:25] XAUUSD exit: tp1_measured_move 15.2 @ 2635.03000 ...

session closed at 2026-01-19T16:25
  balance    $1,943,305.13   (from $500,000)
  status     ACTIVE   can_trade=True
```

Useful flags: `--data ./data` to replay real CSVs instead of synthetic bars,
`--profile titan_entry` for the 0.5%/Method C rule set, `--calendar cal.csv`
to enable the Ch XII-A6 news blackout, `--quiet` for summary only.

## The CLI

Every command works as `python main.py <cmd>` or `titan-tfbs <cmd>`:

```bash
titan-tfbs demo                       # full pipeline, synthetic, ~20s
titan-tfbs instruments                # Ch I universe + contract specs
titan-tfbs checklist                  # Appendix A pre-trade checklist
titan-tfbs config --config config/titan.yaml   # effective config as JSON

titan-tfbs scan --data ./data --symbols XAUUSD,NQ
titan-tfbs backtest --data ./data --symbols XAUUSD,EURUSD \
    --config config/titan.yaml --journal-dir ./journal
```

All commands exit 0 on success. `--json` on `demo`/`scan`/`backtest` emits
machine-readable output.

`--data` wants a directory of `SYMBOL.csv` files with 5M OHLCV bars
(`timestamp,open,high,low,close,volume`). Generate throwaway test data with:

```python
from datetime import datetime, timezone
from titan_tfbs.data.synthetic import firm_scenario, COMPACT_PLAN
from titan_tfbs.data.feed import write_csv
s = datetime(2026, 1, 5, tzinfo=timezone.utc)
write_csv("data/XAUUSD.csv", firm_scenario(s, 2650.0, 0.022, seed=17, plan=COMPACT_PLAN))
```

## Gotchas that will waste your time

- **`--no-journal` blocks every trade, and that is correct.** Ch XII-A4 makes
  journalling mandatory, so the Appendix A checklist refuses to authorise a
  trade with the journal disabled. If a run reports setups evaluated but zero
  executed, check this first.
- **`scan` deliberately stops after SCORE.** It reports formations and scored
  setups without sizing or checklisting them. Zero *executed* trades from
  `scan` is not a bug — use `backtest` or `live_session.py` to see fills.
- **Synthetic runs need `cfg.mtf.min_bias_bars = 20`.** Production wants 60
  daily bars for the Ch IX Screen 1 bias; short synthetic series do not have
  them, and the bias silently reads "insufficient history" (neutral, 1 point).
  `live_session.py` sets this automatically in synthetic mode.
- **Backtests want warmup.** `--warmup N` consumes N base bars to build the
  higher-timeframe screens before trading. `--warmup 0` is fine for synthetic
  scenarios that already begin with a long prior trend.
- **PyYAML is optional, but only for the shipped config.** Without it,
  `main.py` warns and falls back to the built-in firm defaults, which match
  `config/titan.yaml` value for value. An *explicitly* passed `--config` never
  falls back — it errors, because substituting different risk parameters for
  the ones a trader asked for is not a graceful degradation.
- **Demo numbers are not evidence.** Synthetic data is textbook formations
  followed by completed measured moves, so win rates near 100% and 20R+
  averages are properties of the data. Never report them as performance.

## Tests

```bash
python -m pytest tests/ -q       # 153 tests, ~43s
```

`tests/test_integration.py` is the slow one (~34s); it shares one compact
scenario across the heavy cases via a module-scoped `scenario` fixture. If you
add a test that needs full-length data, expect it to cost ~7s per symbol.

## Going live

`execution.dry_run` defaults to `true`. Live trading needs a `Broker`
implementation (`open_position`, `close_position`, `modify_stop` — see
`titan_tfbs/execution/broker.py`) passed to `TFBSBot(broker=...)`, and
`dry_run: false`. Nothing above that layer knows whether fills are simulated.
