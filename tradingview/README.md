# TFBS on TradingView

`titan_tfbs_strategy.pine` is a Pine Script v6 port of the Titan Formation
Breakout System. TradingView runs Pine, not Python, so this is a genuine port
rather than a wrapper — it re-implements the Ch VI-A pipeline inside Pine's
single-symbol, single-chart execution model.

**The Python engine in this repo remains the authority.** Pine cannot see your
other positions, so the portfolio-level rules in Ch VIII-A are outside its
reach. Details under [What Pine cannot enforce](#what-pine-cannot-enforce).

---

## Installing it

1. Open TradingView → any chart → **Pine Editor** (bottom panel).
2. **Open → New strategy**, select everything, delete it.
3. Paste the whole of `titan_tfbs_strategy.pine`.
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

A separate alerts-only **indicator** version (for scanning a watchlist, where a
strategy is impractical) is a straightforward trim of this file. It is not
included yet, because two copies of 850 lines drift apart quickly; ask and it
can be generated from this one.
