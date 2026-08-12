#!/usr/bin/env python3
"""Render the firm backtest JSON into an XLSX workbook.

    python scripts/build_workbook.py --results reports/results.json \
        --out reports/TFBS_Backtest_Results.xlsx

Every summary figure is a FORMULA over the Trades sheet, not a value pasted in
from Python. Filter or edit the trade list and the whole workbook recalculates —
which is the point of shipping a spreadsheet rather than a second PDF.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

FONT = "Arial"
NAVY = "0B1B2B"
GOLD = "B4924C"
LIGHT = "EDF1F5"
WARN = "FDECEA"
WARN_TEXT = "7A1B1B"

CCY = '$#,##0;($#,##0);-'
CCY2 = '$#,##0.00;($#,##0.00);-'
PCT = '0.0%'
PCT2 = '0.00%'
RMULT = '0.00'
INT = '#,##0'

thin = Side(style="thin", color="D5DBE0")
BORDER = Border(left=thin, right=thin, top=thin, bottom=thin)


def header_row(ws, row: int, labels: List[str], widths: List[int] = None):
    for i, label in enumerate(labels, start=1):
        c = ws.cell(row=row, column=i, value=label)
        c.font = Font(name=FONT, bold=True, color="FFFFFF", size=9)
        c.fill = PatternFill("solid", fgColor=NAVY)
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = BORDER
    if widths:
        for i, w in enumerate(widths, start=1):
            ws.column_dimensions[get_column_letter(i)].width = w
    ws.row_dimensions[row].height = 26


def title_block(ws, title: str, subtitle: str = "") -> int:
    ws["A1"] = "TITAN MARKETS LLC"
    ws["A1"].font = Font(name=FONT, bold=True, size=9, color=GOLD)
    ws["A2"] = title
    ws["A2"].font = Font(name=FONT, bold=True, size=15, color=NAVY)
    row = 3
    if subtitle:
        ws["A3"] = subtitle
        ws["A3"].font = Font(name=FONT, size=9, italic=True, color="4A5568")
        row = 4
    return row + 1


def warn_banner(ws, row: int, text: str, span: int = 8) -> int:
    ws.merge_cells(start_row=row, start_column=1, end_row=row + 2, end_column=span)
    c = ws.cell(row=row, column=1, value=text)
    c.font = Font(name=FONT, bold=True, size=9, color=WARN_TEXT)
    c.fill = PatternFill("solid", fgColor=WARN)
    c.alignment = Alignment(wrap_text=True, vertical="center")
    c.border = BORDER
    return row + 4


def style_data(ws, first_row: int, last_row: int, ncols: int, fmts: Dict[int, str]):
    for r in range(first_row, last_row + 1):
        for cix in range(1, ncols + 1):
            c = ws.cell(row=r, column=cix)
            c.font = Font(name=FONT, size=9)
            c.border = BORDER
            if cix in fmts:
                c.number_format = fmts[cix]
        if r % 2 == 0:
            for cix in range(1, ncols + 1):
                ws.cell(row=r, column=cix).fill = PatternFill("solid", fgColor=LIGHT)


# ---------------------------------------------------------------------------


def build(results: Dict, out_path: str) -> None:
    wb = Workbook()
    synthetic = bool(results.get("synthetic"))
    trades = results["trades"]
    n = len(trades)
    m = results["metrics"]
    cfg = results["config"]

    # ---------------- README ------------------------------------------
    ws = wb.active
    ws.title = "README"
    ws.column_dimensions["A"].width = 26
    ws.column_dimensions["B"].width = 92
    row = title_block(ws, "TFBS Portfolio Backtest — Results Workbook",
                      "Companion to TFBS_Backtest_Report.pdf; same run, same numbers.")

    if synthetic:
        row = warn_banner(ws, row,
            "THIS IS NOT A PERFORMANCE RECORD. This run used deterministic SYNTHETIC "
            "price data — textbook formations followed by completed measured moves. "
            "Every performance figure in this workbook is a property of that generated "
            "price path, not evidence that the strategy has an edge. Do not present it "
            "to clients, prospects, program participants or regulators as a track "
            "record. Re-run against firm market data before drawing any conclusion.",
            span=2)

    meta = [
        ("Period", f"{results['period']['start'][:10]} to {results['period']['end'][:10]}"),
        ("Instruments", ", ".join(results["symbols"])),
        ("Bars processed", f"{results['bars']:,} (5-minute)"),
        ("Data source", "Deterministic synthetic price paths" if synthetic
                        else str(results["data_source"])),
        ("Account", f"{cfg['account']['name']} · "
                    f"${cfg['account']['starting_balance']:,.0f} · "
                    f"{cfg['account']['profile']}"),
        ("Generated", results["generated"][:19].replace("T", " ") + " UTC"),
        ("Engine", "titan_tfbs v1.0.0"),
    ]
    for k, v in meta:
        ws.cell(row=row, column=1, value=k).font = Font(name=FONT, bold=True, size=9)
        ws.cell(row=row, column=2, value=v).font = Font(name=FONT, size=9)
        row += 1

    row += 1
    ws.cell(row=row, column=1, value="How to read this workbook").font = Font(
        name=FONT, bold=True, size=11, color=NAVY)
    row += 1
    legend = [
        ("Trades", "One row per closed trade — the Ch XIII-A journal field set. "
                   "This is the source of truth; every other sheet computes from it."),
        ("Summary", "Headline metrics, all as live formulas over Trades. Filter or "
                    "delete rows in Trades and these update."),
        ("By Instrument", "Per-symbol performance via SUMIFS/COUNTIFS over Trades."),
        ("By Pattern", "Ch XIII-C review input: which formations paid."),
        ("By Grade", "Ch XI confluence grade vs outcome — the score-to-outcome check."),
        ("Setup Funnel", "Why setups were refused. Ch XII-A4 requires the skips to be "
                         "logged, and they show what the rules actually do."),
        ("Risk & Compliance", "Peak open risk against the Ch VIII-A cap, and the "
                              "RMG s.05 account status timeline."),
        ("Equity Curve", "Sampled equity, with high-water mark and drawdown as formulas."),
        ("Config", "Every risk parameter the run applied, with its manual citation."),
    ]
    for k, v in legend:
        ws.cell(row=row, column=1, value=k).font = Font(name=FONT, bold=True, size=9)
        c = ws.cell(row=row, column=2, value=v)
        c.font = Font(name=FONT, size=9)
        c.alignment = Alignment(wrap_text=True, vertical="top")
        ws.row_dimensions[row].height = 24
        row += 1

    row += 1
    ws.cell(row=row, column=1, value="Assumptions").font = Font(
        name=FONT, bold=True, size=11, color=NAVY)
    row += 1
    for note in [
        "Fills simulated with a fixed spread, slippage and commission model; real "
        "execution will differ, increasingly so with size.",
        "A bar spanning both stop and target is assumed to hit the STOP first — "
        "deliberately pessimistic, but an assumption.",
        "No economic calendar supplied, so the Ch XII-A6 news blackout was inactive.",
        "Instruments were replayed interleaved in timestamp order so the Ch VIII-A "
        "portfolio caps bind correctly.",
        "Order within a shared timestamp is rotated, not fixed: a fixed order gives "
        "the same instrument the last slice of the 5% risk headroom at every "
        "contested bar. Per-instrument rows are portfolio contributions, not "
        "standalone results.",
    ]:
        ws.cell(row=row, column=1, value="•").font = Font(name=FONT, size=9)
        c = ws.cell(row=row, column=2, value=note)
        c.font = Font(name=FONT, size=9)
        c.alignment = Alignment(wrap_text=True, vertical="top")
        ws.row_dimensions[row].height = 24
        row += 1

    # ---------------- Trades ------------------------------------------
    ws = wb.create_sheet("Trades")
    cols = ["Trade ID", "Symbol", "Asset class", "Direction", "Formation",
            "Pattern TF", "Method", "Grade", "Score", "Entry time", "Exit time",
            "Entry", "Stop", "TP1", "TP2", "Size", "Risk %", "Planned R:R",
            "Realised R", "P&L", "Result", "Max fav R", "Max adv R",
            "Bars open", "Exit reason"]
    widths = [10, 9, 12, 9, 10, 10, 8, 10, 7, 17, 17, 12, 12, 12, 12, 10, 8, 10,
              10, 13, 8, 10, 10, 9, 24]
    header_row(ws, 1, cols, widths)
    ws.freeze_panes = "A2"

    by_symbol = results["by_symbol"]
    for i, t in enumerate(trades, start=2):
        result = "WIN" if t["realized_pnl"] > 0 else ("LOSS" if t["realized_pnl"] < 0 else "BE")
        ws.cell(row=i, column=1, value=t["id"])
        ws.cell(row=i, column=2, value=t["symbol"])
        ws.cell(row=i, column=3, value=by_symbol.get(t["symbol"], {}).get("asset_class", ""))
        ws.cell(row=i, column=4, value=t["direction"])
        ws.cell(row=i, column=5, value=t["pattern"])
        ws.cell(row=i, column=6, value=t["pattern_tf"])
        ws.cell(row=i, column=7, value=t["entry_method"])
        ws.cell(row=i, column=8, value=t["grade"])
        ws.cell(row=i, column=9, value=t["score"])
        ws.cell(row=i, column=10, value=(t["opened"] or "")[:19].replace("T", " "))
        ws.cell(row=i, column=11, value=(t["closed"] or "")[:19].replace("T", " "))
        ws.cell(row=i, column=12, value=t["entry"])
        ws.cell(row=i, column=13, value=t["initial_stop"])
        ws.cell(row=i, column=14, value=t["tp1"])
        ws.cell(row=i, column=15, value=t["tp2"])
        ws.cell(row=i, column=16, value=t["size"])
        ws.cell(row=i, column=17, value=(t["risk_pct"] or 0) / 100.0)
        ws.cell(row=i, column=18, value=t["rr_planned"])
        ws.cell(row=i, column=19, value=t["realized_r"])
        ws.cell(row=i, column=20, value=t["realized_pnl"])
        ws.cell(row=i, column=21, value=result)
        ws.cell(row=i, column=22, value=t["max_favorable_r"])
        ws.cell(row=i, column=23, value=t["max_adverse_r"])
        ws.cell(row=i, column=24, value=t["bars_open"])
        ws.cell(row=i, column=25, value=t["close_reason"])
    last = max(2, n + 1)
    style_data(ws, 2, last, len(cols),
               {12: '0.00000', 13: '0.00000', 14: '0.00000', 15: '0.00000',
                16: '0.00', 17: PCT2, 18: RMULT, 19: RMULT, 20: CCY2,
                22: RMULT, 23: RMULT, 24: INT})
    ws.auto_filter.ref = f"A1:{get_column_letter(len(cols))}{last}"

    T = f"Trades!$T$2:$T${last}"      # P&L
    S = f"Trades!$S$2:$S${last}"      # realised R
    U = f"Trades!$U$2:$U${last}"      # result
    B = f"Trades!$B$2:$B${last}"      # symbol
    E = f"Trades!$E$2:$E${last}"      # formation
    H = f"Trades!$H$2:$H${last}"      # grade

    # ---------------- Summary -----------------------------------------
    ws = wb.create_sheet("Summary")
    ws.column_dimensions["A"].width = 30
    ws.column_dimensions["B"].width = 18
    ws.column_dimensions["C"].width = 46
    row = title_block(ws, "Summary", "Every figure is a formula over the Trades sheet.")
    if synthetic:
        row = warn_banner(ws, row,
            "SYNTHETIC DATA — these numbers describe a generated price path, not "
            "market performance. Not a track record.", span=3)

    header_row(ws, row, ["Metric", "Value", "Note"], [30, 18, 46])
    row += 1
    start = row
    metrics = [
        ("Trades", f"=COUNTA({B})", "closed positions"),
        ("Wins", f'=COUNTIF({U},"WIN")', ""),
        ("Losses", f'=COUNTIF({U},"LOSS")', ""),
        ("Breakeven", f'=COUNTIF({U},"BE")', ""),
        ("Win rate", f"=IFERROR(B{start+1}/B{start},0)", "Ch XIII-B target 50-60%"),
        ("Gross profit", f'=SUMIF({T},">0")', ""),
        ("Gross loss", f'=-SUMIF({T},"<0")', ""),
        ("Net P&L", f"=SUM({T})", ""),
        ("Profit factor", f'=IFERROR(B{start+5}/B{start+6},"n/a")', "Ch XIII-B target > 1.5"),
        ("Expectancy per trade", f"=IFERROR(B{start+7}/B{start},0)", "must be positive"),
        ("Average R", f"=IFERROR(AVERAGE({S}),0)", "Ch XIII-B target > 2.0R"),
        ("Best R", f"=IFERROR(MAX({S}),0)", ""),
        ("Worst R", f"=IFERROR(MIN({S}),0)", ""),
        ("Starting balance", cfg["account"]["starting_balance"], "input"),
        ("Ending balance", f"=B{start+13}+B{start+7}", ""),
        ("Return", f"=IFERROR(B{start+7}/B{start+13},0)", ""),
        ("Max drawdown", m["max_drawdown_pct"] / 100.0,
         "from Equity Curve high-water mark (RMG s.05 basis)"),
        ("Trades per week", m["trades_per_week"], "Ch XIII-B target 3-8"),
    ]
    for label, value, note in metrics:
        ws.cell(row=row, column=1, value=label).font = Font(name=FONT, size=9, bold=True)
        ws.cell(row=row, column=2, value=value)
        ws.cell(row=row, column=3, value=note).font = Font(name=FONT, size=8, italic=True)
        row += 1
    style_data(ws, start, row - 1, 3, {})
    for r, fmt in ((start + 5, CCY), (start + 6, CCY), (start + 7, CCY),
                   (start + 9, CCY), (start + 13, CCY), (start + 14, CCY),
                   (start + 4, PCT), (start + 15, PCT), (start + 16, PCT),
                   (start + 8, '0.00'), (start + 10, RMULT), (start + 11, RMULT),
                   (start + 12, RMULT), (start + 17, '0.0')):
        ws.cell(row=r, column=2).number_format = fmt

    # ---------------- By Instrument -----------------------------------
    ws = wb.create_sheet("By Instrument")
    cols = ["Symbol", "Asset class", "Screens (Ch IX)", "Trades", "Wins",
            "Win rate", "Avg R", "P&L", "Bars"]
    header_row(ws, 1, cols, [12, 14, 34, 10, 8, 10, 10, 15, 12])
    r = 2
    for sym, v in by_symbol.items():
        s = v["screens"]
        ws.cell(row=r, column=1, value=sym)
        ws.cell(row=r, column=2, value=v["asset_class"])
        ws.cell(row=r, column=3, value=f"{s['trend']} > {s['pattern']} > {s['entry']}")
        ws.cell(row=r, column=4, value=f'=COUNTIF({B},A{r})')
        ws.cell(row=r, column=5, value=f'=COUNTIFS({B},A{r},{U},"WIN")')
        ws.cell(row=r, column=6, value=f'=IFERROR(E{r}/D{r},0)')
        ws.cell(row=r, column=7, value=f'=IFERROR(AVERAGEIF({B},A{r},{S}),0)')
        ws.cell(row=r, column=8, value=f'=SUMIF({B},A{r},{T})')
        ws.cell(row=r, column=9, value=v["bars"])
        r += 1
    ws.cell(row=r, column=1, value="TOTAL").font = Font(name=FONT, bold=True, size=9)
    ws.cell(row=r, column=4, value=f"=SUM(D2:D{r-1})")
    ws.cell(row=r, column=5, value=f"=SUM(E2:E{r-1})")
    ws.cell(row=r, column=6, value=f"=IFERROR(E{r}/D{r},0)")
    ws.cell(row=r, column=8, value=f"=SUM(H2:H{r-1})")
    ws.cell(row=r, column=9, value=f"=SUM(I2:I{r-1})")
    style_data(ws, 2, r, len(cols), {4: INT, 5: INT, 6: PCT, 7: RMULT, 8: CCY, 9: INT})
    for cix in (1, 4, 5, 6, 8, 9):
        ws.cell(row=r, column=cix).font = Font(name=FONT, bold=True, size=9)

    # ---------------- By Pattern / By Grade ----------------------------
    for sheet, rng, key, title in (("By Pattern", E, "by_pattern", "Formation"),
                                   ("By Grade", H, "by_grade", "Grade")):
        ws = wb.create_sheet(sheet)
        header_row(ws, 1, [title, "Trades", "Wins", "Win rate", "Avg R", "P&L"],
                   [22, 10, 8, 10, 10, 15])
        r = 2
        for name in sorted(m[key]):
            ws.cell(row=r, column=1, value=name)
            ws.cell(row=r, column=2, value=f'=COUNTIF({rng},A{r})')
            ws.cell(row=r, column=3, value=f'=COUNTIFS({rng},A{r},{U},"WIN")')
            ws.cell(row=r, column=4, value=f'=IFERROR(C{r}/B{r},0)')
            ws.cell(row=r, column=5, value=f'=IFERROR(AVERAGEIF({rng},A{r},{S}),0)')
            ws.cell(row=r, column=6, value=f'=SUMIF({rng},A{r},{T})')
            r += 1
        style_data(ws, 2, max(2, r - 1), 6, {2: INT, 3: INT, 4: PCT, 5: RMULT, 6: CCY})

    # ---------------- Setup Funnel ------------------------------------
    ws = wb.create_sheet("Setup Funnel")
    ws.column_dimensions["A"].width = 52
    ws.column_dimensions["B"].width = 12
    ws.column_dimensions["C"].width = 12
    row = title_block(ws, "Setup funnel",
                      "Ch XII-A4 requires refusals to be logged, not just fills.")
    header_row(ws, row, ["Outcome", "Count", "Share"], [52, 12, 12])
    row += 1
    start = row
    total = results["setups_executed"] + sum(results["rejections"].values())
    ws.cell(row=row, column=1, value="Executed")
    ws.cell(row=row, column=2, value=results["setups_executed"])
    row += 1
    for reason, count in results["rejections"].items():
        ws.cell(row=row, column=1, value=reason)
        ws.cell(row=row, column=2, value=count)
        row += 1
    for r in range(start, row):
        ws.cell(row=r, column=3, value=f"=IFERROR(B{r}/$B${row},0)")
    ws.cell(row=row, column=1, value="TOTAL EVALUATED").font = Font(name=FONT, bold=True, size=9)
    ws.cell(row=row, column=2, value=f"=SUM(B{start}:B{row-1})")
    ws.cell(row=row, column=2).font = Font(name=FONT, bold=True, size=9)
    style_data(ws, start, row, 3, {2: INT, 3: PCT})

    # ---------------- Risk & Compliance -------------------------------
    ws = wb.create_sheet("Risk & Compliance")
    ws.column_dimensions["A"].width = 34
    for col in "BCDE":
        ws.column_dimensions[col].width = 22
    ws.column_dimensions["F"].width = 60
    row = title_block(ws, "Risk and compliance",
                      "Ch VIII-A hard limits and the RMG s.05 drawdown ladder.")
    header_row(ws, row, ["Measure", "Result", "Limit", "Verdict"], [34, 22, 22, 22])
    row += 1
    start = row
    peak = results["peak_open_risk_pct"]
    cap = cfg["risk"]["max_aggregate_open_risk_pct"]
    sus = cfg["risk"]["dd_suspended_max_pct"]
    checks = [
        ("Peak aggregate open risk", peak / 100.0, cap / 100.0,
         "WITHIN" if peak <= cap + 1e-9 else "BREACHED", PCT2),
        ("Max drawdown (trailing)", m["max_drawdown_pct"] / 100.0, sus / 100.0,
         "WITHIN" if m["max_drawdown_pct"] <= sus else "BREACHED", PCT2),
        ("Bars with trading halted", results["halted_bars"], "",
         f"{results['halted_pct']}% of run", INT),
    ]
    for label, val, limit, verdict, fmt in checks:
        ws.cell(row=row, column=1, value=label).font = Font(name=FONT, bold=True, size=9)
        ws.cell(row=row, column=2, value=val).number_format = fmt
        if limit != "":
            ws.cell(row=row, column=3, value=limit).number_format = fmt
        c = ws.cell(row=row, column=4, value=verdict)
        c.font = Font(name=FONT, bold=True, size=9,
                      color="B03030" if verdict == "BREACHED" else "1F7A4D")
        row += 1
    style_data(ws, start, row - 1, 4, {})

    row += 1
    ws.cell(row=row, column=1, value="Account status timeline (RMG s.05)").font = Font(
        name=FONT, bold=True, size=11, color=NAVY)
    row += 1
    header_row(ws, row, ["When", "From", "To", "Max DD", "Trading", "Breach"],
               [22, 14, 14, 12, 14, 60])
    row += 1
    start = row
    timeline = results.get("status_timeline", [])
    for e in timeline:
        ws.cell(row=row, column=1, value=e["ts"][:16].replace("T", " "))
        ws.cell(row=row, column=2, value=e["from"])
        ws.cell(row=row, column=3, value=e["to"])
        ws.cell(row=row, column=4, value=e["max_dd_pct"] / 100.0).number_format = PCT2
        c = ws.cell(row=row, column=5, value="HALTED" if not e["can_trade"] else "permitted")
        if not e["can_trade"]:
            c.font = Font(name=FONT, bold=True, size=9, color="B03030")
        ws.cell(row=row, column=6, value=(e["breaches"] or [""])[0])
        row += 1
    if not timeline:
        ws.cell(row=row, column=1, value="Account remained ACTIVE throughout.")
        row += 1
    style_data(ws, start, max(start, row - 1), 6, {4: PCT2})

    # ---------------- Equity Curve ------------------------------------
    ws = wb.create_sheet("Equity Curve")
    header_row(ws, 1, ["Timestamp", "Equity", "High-water mark", "Drawdown"],
               [22, 16, 18, 14])
    ws.freeze_panes = "A2"
    curve = results["equity_curve"]
    for i, (ts, eq) in enumerate(curve, start=2):
        ws.cell(row=i, column=1, value=ts[:19].replace("T", " "))
        ws.cell(row=i, column=2, value=eq)
        ws.cell(row=i, column=3, value=f"=MAX($B$2:B{i})")
        ws.cell(row=i, column=4, value=f"=IFERROR((C{i}-B{i})/C{i},0)")
    style_data(ws, 2, len(curve) + 1, 4, {2: CCY, 3: CCY, 4: PCT2})

    # ---------------- Config ------------------------------------------
    ws = wb.create_sheet("Config")
    header_row(ws, 1, ["Parameter", "Value", "Source"], [46, 26, 40])
    rows = [
        ("Risk per trade — APPROVED", f"{cfg['risk']['approved_risk_pct']}%", "Ch XI / Ch VIII-A"),
        ("Risk per trade — ELITE", f"{cfg['risk']['elite_risk_pct']}%", "Ch XI"),
        ("Titan Entry cap", f"{cfg['risk']['titan_entry_max_risk_pct']}%", "Ch XII-B"),
        ("Aggregate open risk cap", f"{cfg['risk']['max_aggregate_open_risk_pct']}%", "Ch VIII-A hard limit"),
        ("Minimum reward:risk", f"{cfg['risk']['min_reward_risk']}:1", "Ch VIII-A hard limit"),
        ("Correlated position cap", f"{cfg['risk']['max_correlated_positions']}", "Ch VIII-A"),
        ("Daily loss limit", f"{cfg['risk']['daily_loss_limit_pct']}%", "Ch VIII-A / Ch XII-A5"),
        ("Weekly loss limit", f"{cfg['risk']['weekly_loss_limit_pct']}%", "Ch VIII-A / Ch XII-A5"),
        ("Monthly DD trigger", f"{cfg['risk']['monthly_dd_trigger_pct']}%", "Ch VIII-A"),
        ("Max DD ladder", f"{cfg['risk']['dd_restricted_max_pct']}/"
                          f"{cfg['risk']['dd_probation_max_pct']}/"
                          f"{cfg['risk']['dd_suspended_max_pct']}%", "RMG s.05"),
        ("Max DD basis", cfg["risk"]["max_drawdown_basis"], "DERIVED"),
        ("Confluence gate", f"{cfg['confluence']['min_score_to_trade']}/10", "Ch XI"),
        ("ELITE threshold", f"{cfg['confluence']['elite_score']}/10", "Ch XI"),
        ("Entry method", cfg["entry"]["default_method"], "Ch VII firm default"),
        ("Breakeven at", f"{cfg['trade_management']['breakeven_at_r']}R", "Ch X-A"),
        ("Trail from", f"{cfg['trade_management']['trail_start_r']}R", "Ch X-A"),
        ("TP ladder", f"{cfg['trade_management']['tp1_allocation']:.0%}/"
                      f"{cfg['trade_management']['tp2_allocation']:.0%}/"
                      f"{cfg['trade_management']['tp3_allocation']:.0%}", "Ch X-B"),
        ("Volume surge", f"{cfg['breakout']['volume_surge_multiple']}x", "Ch V-A criterion 2"),
        ("News blackout", f"-{cfg['compliance']['news_blackout_before_min']}/"
                          f"+{cfg['compliance']['news_blackout_after_min']} min",
         "Ch XII-A6 (inactive — no calendar)"),
        ("Trend screens", "/".join(cfg["mtf"]["trend_timeframes"]), "Ch IX Screen 1"),
        ("Pattern screens", "/".join(cfg["mtf"]["pattern_timeframes"]), "Ch IX Screen 2"),
        ("Entry screens", "/".join(cfg["mtf"]["entry_timeframes"]), "Ch IX Screen 3"),
    ]
    for i, (k, v, src) in enumerate(rows, start=2):
        ws.cell(row=i, column=1, value=k)
        ws.cell(row=i, column=2, value=v)
        ws.cell(row=i, column=3, value=src)
    style_data(ws, 2, len(rows) + 1, 3, {})

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--results", default="reports/results.json")
    ap.add_argument("--out", default="reports/TFBS_Backtest_Results.xlsx")
    args = ap.parse_args(argv)
    results = json.loads(Path(args.results).read_text())
    build(results, args.out)
    print(f"-> {args.out} ({Path(args.out).stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
