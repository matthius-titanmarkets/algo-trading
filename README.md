# Titan Formation Breakout System (TFBS)

Automated implementation of the **sole approved trading strategy of Titan
Markets LLC**, built from two firm documents:

| Source | Document |
| --- | --- |
| **TFBS** | *TFBS Master Strategy Manual*, v1.0, April 2026 — Matthius Douglas, Founder & CEO |
| **RMG** | *The Beginner's Complete Guide to Risk Management in Prop Trading*, 2026 Edition |

The bot scans the firm's instruments for Head & Shoulders and Double/Triple
Top-Bottom formations, waits for a confirmed breakout, scores the setup against
the Confluence Scoring System, runs the Appendix A pre-trade checklist, sizes
the position against the firm's risk limits, executes, and manages the trade to
the Ch X exit architecture — journalling everything, including the setups it
passes on.

> Every parameter in `config/titan.yaml` cites the chapter it comes from.
> Parameters marked `DERIVED` are engineering choices needed to make a prose
> rule machine-executable; they are all overridable. See
> [Interpretation notes](#interpretation-notes).

---

## Quick start

```bash
# No third-party dependencies are needed for the engine itself.
python -m titan_tfbs demo                     # deterministic synthetic run
python -m titan_tfbs instruments              # the Ch I tradeable universe
python -m titan_tfbs checklist                # the Appendix A checklist

# Research: formations and scored setups, no orders
python -m titan_tfbs scan --data ./data --symbols XAUUSD,NQ

# Full pipeline against history, with the risk gate and checklist applied
python -m titan_tfbs backtest --data ./data --symbols XAUUSD,EURUSD \
    --config config/titan.yaml --journal-dir ./journal
```

`--data` is a directory of `SYMBOL.csv` files with 5-minute OHLCV bars
(`timestamp,open,high,low,close,volume`; most column spellings and both ISO and
epoch timestamps are accepted). Every higher timeframe is derived from that one
series, so the three screens can never disagree because of vendor differences.

Run the tests with `python -m pytest tests/ -q` (150 tests, ~40s).

---

## The pipeline

The engine implements Ch VI-A end to end:

```
1. SCAN      patterns/detector.py     H&S / DT-DB on the pattern screen
2. VALIDATE  patterns/*.py            Ch III-E and Ch IV-C quality filters
3. CONFIRM   strategy/breakout.py     Ch V — closing break, then the retest
4. SCORE     strategy/confluence.py   Ch XI — mandatory, minimum 7/10
5. SIZE      risk/                    Ch VIII — limits, then position size
6. EXECUTE   execution/               Ch VII entry methods A / B / C
7. MANAGE    execution/manager.py     Ch X — BE at 1R, trail at 1.5R, TP1/2/3
             journal/                 Ch XIII — every trade, including skips
```

`bot.py` wires steps 5-7 to an account; `strategy/tfbs.py` owns 1-4.

### Multi-timeframe protocol (Ch IX)

The firm's research and bias work happens on the higher timeframes; the 5M and
15M charts exist only to time the entry.

| Screen | Timeframe | Function |
| --- | --- | --- |
| 1 — TREND | Daily / Weekly | Macro directional bias |
| 2 — PATTERN | 4H / 1H | Formation identification |
| 3 — ENTRY | 15M / 5M | Breakout candle, retest, volume, rejection |

Counter-trend setups score 0 on the Ch XI HTF factor **and** fail the
mandatory Appendix A item "Is Screen 1 aligned with trade direction?", so they
are blocked, not merely penalised.

### What is *not* a TFBS setup

Per Ch VI-B, a level break with no H&S or DT/DB context is graded **F —
"NOT a TFBS setup — skip"**, and a formation with an intact neckline is
**watchlist only**. Both are enforced in code: nothing outside
`patterns/head_shoulders.py` and `patterns/double_top.py` can produce a
tradeable formation, and no signal exists until `strategy/breakout.py` confirms
a close through the key level. Ch XII-A1's ban on anticipation trading is
structural here, not advisory.

---

## Risk and compliance

| Rule | Limit | Source |
| --- | --- | --- |
| Risk per trade | 1-2% (prop desk), 0.5% (Titan Entry) | TFBS Ch VIII-A, Ch XII-B |
| Aggregate open risk | 5% | TFBS Ch VIII-A |
| Minimum reward:risk | 2:1, below = skip | TFBS Ch VIII-A |
| Correlated exposure | 2 per currency/sector | TFBS Ch VIII-A |
| Daily loss limit | 3% — cease trading for the day | TFBS Ch VIII-A, Ch XII-A5 |
| Weekly loss limit | 6% — mandatory cessation | TFBS Ch VIII-A, Ch XII-A5 |
| Monthly drawdown | 10% — mandatory CEO review | TFBS Ch VIII-A |
| Averaging down | Prohibited, critical violation | TFBS Ch XII-A2, Ch XIV-A5 |
| News blackout | -30 min / +5 min around high impact | TFBS Ch XII-A6, Appendix A |

The RMG s.05 drawdown ladder runs alongside these as a state machine:

| Status | Daily DD | Max DD | Consequence |
| --- | --- | --- | --- |
| ACTIVE | <= 2% | <= 8% | Trading permitted |
| RESTRICTED | 2-3% | 8-10% | Position size capped at 50% |
| PROBATION | 3-4% | 10-12% | Blocked until the session review is signed off |
| SUSPENDED | > 4% | > 12% | Blocked pending evaluation |

Each gate is independent: clearing the probation review does not clear an
unacknowledged CEO trigger, and vice versa.

Compliance violations escalate YELLOW -> ORANGE (size halved) -> RED (trading
suspended) per Ch XII-C.

### Position sizing across asset classes

Ch VIII-B gives `Position Size = (Balance x Risk %) / |Entry - Stop|`. That
assumes a unit value of 1 and a quote currency equal to the account currency.
`risk/sizing.py` generalises it with the instrument's value per price point and
a quote-to-account conversion, and reduces exactly to the manual's formula for a
USD-quoted instrument in a USD account — the Ch VIII-B worked example
(`$500K, 1%, 1.3050 -> 1.3100`) returns 10 lots, and is asserted in the tests.

Sizes always round **down**: rounding up would breach a hard limit.

---

## Configuration

`config/titan.yaml` is the firm configuration, fully annotated. Load it with
`--config`, or build a `TitanConfig` in code:

```python
from titan_tfbs import load_config
from titan_tfbs.bot import TFBSBot

cfg = load_config("config/titan.yaml")
bot = TFBSBot(cfg, symbols=["XAUUSD", "NQ"])

for candle in feed:                       # 5M bars
    bot.on_candle("XAUUSD", candle)

print(bot.snapshot())
```

Switch rule sets with `--profile titan_entry`, which caps risk at 0.5% and
forces Method C entries until 30 consecutive positive-expectancy trades
(Ch XII-B).

### Going live

`execution/broker.py` defines a three-method `Broker` interface
(`open_position`, `close_position`, `modify_stop`). `PaperBroker` implements it
with spread crossing, slippage and commission; a venue adapter only has to
implement the same three methods. Nothing above that layer knows whether fills
are simulated. `execution.dry_run` defaults to `true`.

---

## Interpretation notes

The manual is written for human traders, so a few rules needed a defensible
machine reading. Each is configurable, and each is flagged in `config/titan.yaml`:

- **Neckline slope "< 15 degrees" (Ch III-E).** Degrees on a price chart depend
  on the pixel aspect ratio. Implemented in ATR-normalised space: one ATR of
  movement per bar equals 45 degrees.
- **"Peaks within 1-3% of each other" (Ch IV-C).** 3% of EURUSD is 375 pips, so
  proximity is additionally bounded as a fraction of the pattern's own height.
  Set `level_proximity_height_frac` high to trade the rule literally.
- **"Pullback min 10% of pattern height" (Ch IV-C).** Circular as written — the
  pullback *is* the pattern height — so it is measured against the prior impulse
  leg, the only non-circular reading.
- **"Clear established trend" (Ch III-E, Ch IV-C).** Leg size alone cannot
  distinguish a trend from one swing of a range, so the structure of the
  approach is classified as well. Without this, a range's own highs register as
  a Triple Top; there is a test for exactly that.
- **"Choppy macro context" (Ch V-B).** Assessed on the formation at detection
  time — does the apex cap the enclosing range? — rather than on the break,
  whose own follow-through would otherwise extend the range and make every good
  setup look choppy.
- **Stop placement.** Appendix A requires a stop "at the structural
  invalidation point" while Ch VII Method B places it "just beyond retest wick".
  These reconcile because a failed retest *is* the invalidation of the
  structural flip; the retest-wick stop is the default, floored by ATR, spread
  and a fraction of the pattern screen's own volatility.
- **Breakout screen.** Ch IX lists the breakout candle under Screen 3, so a 15M
  close through a 4H neckline confirms by default. Set
  `breakout.confirm_timeframe: pattern` to demand a full 4H close.
- **Ch X-C early exits** are discretionary triggers in the manual. They are
  implemented as automatic and can be switched off individually.

---

## Repository layout

```
titan_tfbs/
  config.py            firm parameters, every value cited
  instruments.py       Ch I universe + contract specs
  core/                candles, timeframes, indicators, market structure
  patterns/            Ch III (H&S) and Ch IV (DT/DB/Triple)
  strategy/            Ch V breakout, Ch IX MTF, Ch XI scoring, Appendix A
  risk/                Ch VIII limits, RMG s.05 ladder, Ch XII compliance
  execution/           orders, broker interface, Ch X trade management
  data/                CSV / in-memory feeds, economic calendar, synthetic data
  journal/             Ch XIII record set
  backtest/            event-driven backtester, Ch XIII-B metrics
  bot.py               live orchestration
  cli.py               command-line interface
config/titan.yaml      the firm configuration
tests/                 150 tests, one per enforced rule
```

The backtester replays candles through the *live* `TFBSBot` — same pipeline,
same risk gate, same trade management — so a backtest measures the system that
would actually trade rather than a parallel implementation of it.

---

## TradingView

`tradingview/titan_tfbs_strategy.pine` is a Pine Script v6 port for charting
and alerts — paste it into the Pine Editor and run it on a 4H or 1H chart. It
implements the same pipeline, scoring and Ch X exit architecture, and emits
JSON alerts suitable for broker webhooks.

It cannot enforce the portfolio-level rules — Pine sees one symbol, so Ch
VIII-A's 5% aggregate open risk and the 2-per-sector correlated cap are outside
its reach, and there is no economic calendar for Ch XII-A6. This engine remains
the authority for those. See `tradingview/README.md`.

---

## A note on the demo numbers

`python -m titan_tfbs demo` runs on deterministic synthetic data containing
textbook formations followed by completed measured moves. Its win rate and R
multiples are properties of that data, not evidence of edge. It exists to prove
the mechanics end to end. Judge the strategy on firm data.

---

*Proprietary & Confidential. (c) 2026 Titan Markets LLC · Chicago, Illinois.*
