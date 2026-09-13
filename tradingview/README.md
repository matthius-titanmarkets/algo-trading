# TFBS on TradingView

Two Pine Script v6 builds of the Titan Formation Breakout System live here.
TradingView runs Pine, not Python, so these are genuine ports rather than
wrappers — each re-implements the Ch VI-A pipeline inside Pine's single-symbol,
single-chart execution model.

| File | Build | Use it for |
| --- | --- | --- |
| `titan_tfbs_strategy.pine` | `strategy()` | Backtesting. The Strategy Tester gives you the Ch XIII-B numbers. |
| `titan_tfbs_indicator.pine` | `indicator()` | Live charting, watchlist scanning and alerts, with the Appendix A checklist on screen. |

Both carry the same pattern detection, breakout protocol, confluence score,
sizing and Ch X management. Pick the strategy when you want a P&L curve; pick
the indicator when you want to *see* the system's reasoning and get alerts
across a basket of symbols. [What the indicator adds](#the-indicator-build)
lists the differences.

**The Python engine in this repo remains the authority.** Pine cannot see your
other positions, so the portfolio-level rules in Ch VIII-A are outside its
reach. Details under [What Pine cannot enforce](#what-pine-cannot-enforce).

---

## Installing it

1. Open TradingView → any chart → **Pine Editor** (bottom panel).
2. **Open → New strategy** (or **New indicator** for the indicator build),
   select everything, delete it.
3. Paste the whole of `titan_tfbs_strategy.pine` — or
   `titan_tfbs_indicator.pine`.
4. **Save**, name it `Titan TFBS`, then **Add to chart**.
5. Set the chart to **4H or 1H** — the Ch IX Screen 2 timeframe.
6. Open the settings gear and set **Trader profile** (`Prop Desk` or
   `Titan Entry`) and your account size under the strategy's *Properties* tab.

The **Strategy Tester** panel then gives you the Ch XIII-B numbers directly:
net profit, profit factor, max drawdown, win rate, and the full list of trades.

### Timeframe matters

Ch IX runs three screens. In Pine, one script sees one chart timeframe, so:

| Screen | Manual | Here |
| --- | --- | --- |
| 1 — TREND | Daily / Weekly | `request.security` on the **Trend timeframe** input |
| 2 — PATTERN | 4H / 1H | **the chart you run it on** |
| 3 — ENTRY | 15M / 5M | folded into Screen 2 |

Run it on 1H if you want entries closer to the manual's precision; run it on 4H
for the firm's swing cadence and fewer, larger setups.

---

## What it enforces

Everything below is live in the script, with the chapter cited in the input's
tooltip:

- **Ch III / Ch IV** — Head & Shoulders, Inverse H&S, Double Top/Bottom and the
  Triple variant, with the mandatory prior-trend and range-context filters and
  the preferred symmetry / neckline-slope / duration / volume filters feeding
  the Ch XI Pattern Quality factor.
- **Ch V** — breakout on a **closing** basis only, the 1.5× volume surge
  criterion, the retest-and-hold zone, and the flip-failure invalidation.
  Wick-only penetrations are marked on the chart and never traded.
- **Ch VII** — entry Methods A / B / C, with B as the firm default and Titan
  Entry forced onto C.
- **Ch VIII** — risk-percent position sizing off the actual stop distance, the
  2:1 minimum R:R as a hard skip, the 3% daily and 6% weekly loss limits, and
  the RMG s.05 2-3% size-cap band.
- **Ch IX** — the Daily/Weekly bias gate; counter-trend setups are blocked.
- **Ch X** — breakeven at 1R (never sooner), trailing from 1.5R on swing or
  20-EMA, the 50/30/20 TP1/TP2/TP3 ladder, and the Ch X-C early exits.
- **Ch XI** — the 10-point confluence score, gated at 7/10, with the Ch IV-D
  triple bonus and R:R below 2:1 as an auto-skip.

The status panel (top right) shows the live bias, formation, state machine,
score, R:R and drawdown status.

---

## What Pine cannot enforce

Do not assume the chart is applying these. They are the reason the Python
engine still runs the book:

| Rule | Why not |
| --- | --- |
| **Ch VIII-A** aggregate open risk ≤ 5% | Pine sees one symbol and one strategy instance. Two charts each risking 2% cannot know about each other. |
| **Ch VIII-A** max 2 correlated positions | Same reason — no cross-symbol view. |
| **Ch XII-A6** news blackout | Pine has no economic calendar. Approximated by a manual session window input, off by default. |
| **Ch XII-C** compliance escalation | Requires state persisted across sessions and accounts. |
| **Ch XIII** journal | The Strategy Tester's trade list is the closest equivalent; it does not carry the Ch XIII-A field set. |

Two further divergences worth knowing:

- **Breakout and retest confirm on the chart timeframe**, not on a separate
  15M/5M screen as Ch IX specifies. Expect slightly different fills from the
  Python engine on the same data.
- **Prior-trend structure** is checked with EMA alignment rather than the
  Python engine's swing-structure classification. It rejects ranges well, but
  it is not the identical test.

---

## Alerts and automation

Both the entries and the Ch X-B exits carry a JSON `alert_message`:

```json
{"firm":"Titan Markets LLC","strategy":"TFBS","symbol":"XAUUSD","tf":"60",
 "action":"SELL","pattern":"H&S","grade":"APPROVED","score":8,"method":"B",
 "entry":2693.4,"sl":2695.54,"tp1":2634.88,"tp2":2626.84,"tp3":2577.37,
 "rr":29.57,"risk_pct":1.25,"qty":30.41}
```

To wire it up: right-click the chart → **Add alert** → Condition: your TFBS
strategy → Message: `{{strategy.order.alert_message}}` → add your broker's
webhook URL. TradingView will POST that JSON on every fill.

There are also two chart alerts for the Ch VI-B watchlist — one when a neckline
**breaks**, one when a **retest confirms** — so you can watch a basket without
running the strategy on every symbol.

---

## Checking edits without TradingView

TradingView owns the only real Pine compiler, so edits made anywhere else are
unverified until you paste them in. `check_pine.py` catches the traps that
produce opaque errors:

```bash
python tradingview/check_pine.py
# → titan_tfbs_indicator.pine: clean — no known Pine traps found
# → titan_tfbs_strategy.pine: clean — no known Pine traps found
```

With no argument it checks every `.pine` file in the directory, so both builds
are covered; pass a path to check one.

It checks continuation indents, block-body indents, bracket balance,
`str.format` placeholders, `ta.*` calls stranded inside conditional branches,
and `:=` on undeclared names. Exit 0 clean, 1 on findings.

The one that matters most: **Pine reads a wrapped line indented by a multiple
of four spaces as a new block**, which is what produced the
`CE10013 — end of line without line continuation` error on the first version of
this file. Block bodies are the exact inverse and must be at a multiple of four.
Both directions are checked.

It is a linter, not a compiler. Clean means the known traps are avoided, not
that it builds.

## Before you trust the backtest

- **Point value.** Sizing uses `syminfo.pointvalue`, which is TradingView's
  contract spec and may not match your broker's. Check one trade's risk by hand
  against your account, and use the **Point value override** input if it is off.
- **`process_orders_on_close` is on.** Entries fill at the close of the
  confirming bar, matching the Ch V "closing basis" rule. Turning it off will
  shift every fill to the next bar's open.
- **Non-repainting by default.** The higher-timeframe bias reads the last
  *closed* Daily/Weekly bar, so live behaviour matches the backtest. The
  "Use last CLOSED higher-TF bar" input can be turned off for a more responsive
  bias, at the cost of a backtest that flatters the live result. Leave it on.
- **Formations confirm late by design.** A pivot needs `swing lookback` bars on
  each side before it exists, so a formation registers a few bars after its
  right shoulder. That is not a lag to tune away — it is what stops the
  detector from inventing patterns out of unfinished price action.
- **Commission and slippage** default to $3.50 per contract and zero slippage.
  Set both to your real costs in the strategy's *Properties* tab before reading
  anything into the equity curve.

---

## Keeping the two in sync

The Pine inputs deliberately mirror `config/titan.yaml` field for field, so a
parameter changed in one can be found in the other. When the manual changes,
change `config/titan.yaml` first — it is the annotated source of truth, and
every value there cites its chapter.

Both Pine files carry the same input names in the same groups, so a value
changed in one can be found in the other and in the YAML.

---

## The indicator build

`titan_tfbs_indicator.pine` runs the same pipeline as an `indicator()`, so it
can be stacked on charts, added to a watchlist scan, and driven off
alertcondition() alerts that a strategy cannot provide. Everything the strategy
enforces, it enforces — and it shows its working:

- **The Appendix A checklist, live.** All sixteen items in their five sections,
  each marked PASS / FAIL / miss with the measured value beside it, mandatory
  items separated from preferred ones. A mandatory failure blocks the signal
  exactly as `strategy.checklist` blocks it in Python.
- **A second trend screen.** Ch IX reads Daily *and* Weekly. Where the strategy
  build takes one, the indicator takes both, applies "higher TF trumps lower
  TF" when they disagree, and carries the divergence forward — it costs the
  setup its second Ch XI alignment point and halves the size, mirroring
  `mtf.analyze` and `MTFAlignment.size_factor`.
- **Real S/R levels.** Swing pivots are clustered into levels with touch counts,
  the way `core.structure.find_levels` does, and those levels drive the Ch XI
  S/R-confluence and clean-path factors and the Ch X-B TP2 ("the next
  significant S/R beyond TP1", with the R-multiple only as a fallback). The
  strategy build approximates all three from a rolling high/low.
- **A paper ledger.** One virtual position per chart, moved by the Ch X rules —
  breakeven at 1R, trailing from 1.5R, the 50/30/20 ladder, the Ch X-C exits —
  against the **Account size** input. That is what makes the 3% daily and 6%
  weekly loss limits and the RMG s.05 size cap enforceable without a
  `strategy.equity` to read. It is a compliance aid, not a backtest: for
  performance numbers use the strategy build.
- **Skipped setups are journalled.** Ch XII-A4 wants the refusals too, so a
  setup that reaches its trigger and is turned away fires an alert naming the
  rule that turned it away, and the panel shows the same reason.
- **Formation drawing.** The neckline, plus the pattern skeleton, plus the
  projected stop and TP1 for a confirmed setup that has not signalled.

### Alerts

Eight `alertcondition()` entries appear in TradingView's alert dialog —
formation armed, neckline broken, retest confirmed, follow-through, approved
signal, approved long, approved short, and wick-only penetration. Pick one,
point it at a symbol list, and you have a Ch VI-B watchlist scan.

For automation use the dynamic `alert()` payloads instead: the approved signal
emits the same broker-webhook JSON as the strategy build, and the paper exit
emits a Ch XIII-A journal line:

```json
{"strategy":"TFBS","action":"PAPER_EXIT","symbol":"XAUUSD","pattern":"H&S",
 "grade":"APPROVED","score":8,"risk_pct":1.25,"reason":"Ch X-C invalidation",
 "pnl":-3120.5,"balance":496879.5,"bars_held":37}
```

Set the alert's message to `{{strategy.order.alert_message}}` for the strategy
build; for the indicator, create the alert on the indicator with **Any alert()
function call** and TradingView will forward each payload as it is emitted.

### What the indicator still cannot do

The single-symbol limits in [What Pine cannot enforce](#what-pine-cannot-enforce)
apply unchanged — the aggregate 5% open risk and the correlated-position cap
need a portfolio view that Pine does not have, so the checklist marks that item
as covering this chart only. The paper ledger also assumes the stop was hit
before any target when both fall inside one bar, which is the conservative
reading and the one the Python backtester takes.
