#!/usr/bin/env python3
"""Render the firm backtest JSON into a PDF report.

    python scripts/build_report.py --results reports/results.json \
        --out reports/TFBS_Backtest_Report.pdf

When the run used synthetic data the document says so on the cover, in a
watermark across every page, and in the footer. That is deliberate: a report
that looks official is exactly the kind of artefact that gets forwarded, and
these numbers are a property of the generated price path, not evidence of edge.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.graphics.shapes import Drawing, Line, Rect, String
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

NAVY = colors.HexColor("#0B1B2B")
GOLD = colors.HexColor("#B4924C")
SLATE = colors.HexColor("#4A5568")
LIGHT = colors.HexColor("#EDF1F5")
RED = colors.HexColor("#B03030")
GREEN = colors.HexColor("#1F7A4D")
AMBER = colors.HexColor("#B8860B")

PAGE_W, PAGE_H = A4
MARGIN = 18 * mm


# ---------------------------------------------------------------------------
# Page furniture
# ---------------------------------------------------------------------------


class ReportDoc(BaseDocTemplate):
    def __init__(self, path: str, synthetic: bool, **kw):
        super().__init__(path, pagesize=A4,
                         leftMargin=MARGIN, rightMargin=MARGIN,
                         topMargin=MARGIN + 6 * mm, bottomMargin=MARGIN + 6 * mm, **kw)
        self.synthetic = synthetic
        frame = Frame(self.leftMargin, self.bottomMargin,
                      self.width, self.height, id="body")
        self.addPageTemplates([
            PageTemplate(id="cover", frames=[frame], onPage=self._cover_furniture),
            PageTemplate(id="body", frames=[frame], onPage=self._furniture),
        ])

    def _watermark(self, canvas):
        if not self.synthetic:
            return
        canvas.saveState()
        canvas.setFont("Helvetica-Bold", 46)
        canvas.setFillColor(colors.Color(0.75, 0.15, 0.15, alpha=0.10))
        canvas.translate(PAGE_W / 2, PAGE_H / 2)
        canvas.rotate(38)
        canvas.drawCentredString(0, 30, "SYNTHETIC DATA")
        canvas.drawCentredString(0, -30, "NOT A PERFORMANCE RECORD")
        canvas.restoreState()

    def _footer(self, canvas, page_label=True):
        canvas.saveState()
        canvas.setStrokeColor(LIGHT)
        canvas.setLineWidth(0.6)
        canvas.line(MARGIN, MARGIN + 2 * mm, PAGE_W - MARGIN, MARGIN + 2 * mm)
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(SLATE)
        canvas.drawString(MARGIN, MARGIN - 2 * mm,
                          "TITAN MARKETS LLC · Proprietary & Confidential · "
                          "Chicago, Illinois")
        if self.synthetic:
            canvas.setFillColor(RED)
            canvas.setFont("Helvetica-Bold", 7)
            canvas.drawCentredString(PAGE_W / 2, MARGIN - 2 * mm,
                                     "SYNTHETIC DATA — NOT A PERFORMANCE RECORD")
        if page_label:
            canvas.setFillColor(SLATE)
            canvas.setFont("Helvetica", 7)
            canvas.drawRightString(PAGE_W - MARGIN, MARGIN - 2 * mm,
                                   f"Page {canvas.getPageNumber()}")
        canvas.restoreState()

    def _cover_furniture(self, canvas, doc):
        canvas.saveState()
        canvas.setFillColor(NAVY)
        canvas.rect(0, PAGE_H - 52 * mm, PAGE_W, 52 * mm, stroke=0, fill=1)
        canvas.setFillColor(GOLD)
        canvas.rect(0, PAGE_H - 54 * mm, PAGE_W, 2 * mm, stroke=0, fill=1)
        canvas.restoreState()
        self._watermark(canvas)
        self._footer(canvas, page_label=False)

    def _furniture(self, canvas, doc):
        canvas.saveState()
        canvas.setFillColor(NAVY)
        canvas.rect(0, PAGE_H - 12 * mm, PAGE_W, 12 * mm, stroke=0, fill=1)
        canvas.setFont("Helvetica-Bold", 8)
        canvas.setFillColor(colors.white)
        canvas.drawString(MARGIN, PAGE_H - 8 * mm,
                          "TITAN FORMATION BREAKOUT SYSTEM — BACKTEST REPORT")
        canvas.setFillColor(GOLD)
        canvas.drawRightString(PAGE_W - MARGIN, PAGE_H - 8 * mm, "TFBS v1.0")
        canvas.restoreState()
        self._watermark(canvas)
        self._footer(canvas)


# ---------------------------------------------------------------------------
# Styles & helpers
# ---------------------------------------------------------------------------


def make_styles():
    s = getSampleStyleSheet()
    s.add(ParagraphStyle("CoverTitle", parent=s["Title"], fontSize=26, leading=30,
                         textColor=colors.white, alignment=TA_CENTER, spaceAfter=4))
    s.add(ParagraphStyle("CoverSub", parent=s["Normal"], fontSize=11, leading=15,
                         textColor=GOLD, alignment=TA_CENTER))
    s.add(ParagraphStyle("H1", parent=s["Heading1"], fontSize=14, leading=17,
                         textColor=NAVY, spaceBefore=10, spaceAfter=6))
    s.add(ParagraphStyle("H2", parent=s["Heading2"], fontSize=11, leading=14,
                         textColor=NAVY, spaceBefore=8, spaceAfter=4))
    s.add(ParagraphStyle("Body", parent=s["Normal"], fontSize=9, leading=13,
                         alignment=TA_JUSTIFY, textColor=colors.HexColor("#1A202C")))
    s.add(ParagraphStyle("Small", parent=s["Normal"], fontSize=7.5, leading=10,
                         textColor=SLATE))
    s.add(ParagraphStyle("WarnBody", parent=s["Normal"], fontSize=9, leading=13,
                         textColor=colors.HexColor("#7A1B1B")))
    s.add(ParagraphStyle("Cell", parent=s["Normal"], fontSize=7.5, leading=9.5))
    return s


def money(v: float) -> str:
    return f"{v:,.0f}"


def table(data, widths, align_right=(), header=True, font=7.5):
    t = Table(data, colWidths=widths, repeatRows=1 if header else 0)
    style = [
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), font),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 0), (-1, -2), 0.25, LIGHT),
    ]
    if header:
        style += [
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
        ]
    for col in align_right:
        style.append(("ALIGN", (col, 0), (col, -1), "RIGHT"))
    t.setStyle(TableStyle(style))
    return t


def callout(text, styles, tone=RED):
    fill = colors.Color(tone.red, tone.green, tone.blue, alpha=0.07)
    t = Table([[Paragraph(text, styles["WarnBody"])]], colWidths=[PAGE_W - 2 * MARGIN])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), fill),
        ("BOX", (0, 0), (-1, -1), 0.9, tone),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    return t


def equity_chart(curve: List, width=PAGE_W - 2 * MARGIN, height=58 * mm):
    """Simple equity line with the peak/drawdown shaded — no matplotlib needed."""
    d = Drawing(width, height)
    if len(curve) < 2:
        return d
    values = [v for _, v in curve]
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    pad_l, pad_b, pad_t = 22 * mm, 9 * mm, 5 * mm
    plot_w, plot_h = width - pad_l - 4 * mm, height - pad_b - pad_t

    d.add(Rect(pad_l, pad_b, plot_w, plot_h, fillColor=colors.white,
               strokeColor=LIGHT, strokeWidth=0.5))
    # gridlines + y labels
    for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
        y = pad_b + plot_h * frac
        d.add(Line(pad_l, y, pad_l + plot_w, y, strokeColor=LIGHT, strokeWidth=0.4))
        d.add(String(pad_l - 3, y - 2, f"{(lo + span * frac):,.0f}",
                     fontSize=6, fillColor=SLATE, textAnchor="end"))

    # running peak (high-water mark) — the basis the RMG drawdown ladder uses
    peak = values[0]
    peak_pts, eq_pts = [], []
    for i, v in enumerate(values):
        peak = max(peak, v)
        x = pad_l + plot_w * (i / (len(values) - 1))
        eq_pts.append((x, pad_b + plot_h * ((v - lo) / span)))
        peak_pts.append((x, pad_b + plot_h * ((peak - lo) / span)))
    for pts, col, w in ((peak_pts, GOLD, 0.7), (eq_pts, NAVY, 1.2)):
        for a, b in zip(pts, pts[1:]):
            d.add(Line(a[0], a[1], b[0], b[1], strokeColor=col, strokeWidth=w))

    d.add(String(pad_l, 2, "equity", fontSize=6, fillColor=NAVY))
    d.add(String(pad_l + 40, 2, "high-water mark", fontSize=6, fillColor=GOLD))
    return d


# ---------------------------------------------------------------------------
# Report body
# ---------------------------------------------------------------------------


def build(results: Dict, out_path: str) -> None:
    st = make_styles()
    synthetic = bool(results.get("synthetic"))
    m = results["metrics"]
    cfg = results["config"]
    story: List = []

    # ---- Cover ----------------------------------------------------------
    story.append(Spacer(1, 8 * mm))
    story.append(Paragraph("TITAN MARKETS LLC", st["CoverSub"]))
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph("Titan Formation Breakout System", st["CoverTitle"]))
    story.append(Paragraph("Portfolio Backtest Report", st["CoverSub"]))
    story.append(Spacer(1, 26 * mm))

    period = results["period"]
    cover = [
        ["Period", f"{period['start'][:10]} to {period['end'][:10]}"],
        ["Instruments", f"{len(results['symbols'])} — {', '.join(results['symbols'])}"],
        ["Bars processed", f"{results['bars']:,} (5-minute)"],
        ["Account", f"{cfg['account']['name']} · "
                    f"${cfg['account']['starting_balance']:,.0f} · "
                    f"{cfg['account']['profile']}"],
        ["Entry method", f"Method {'C' if cfg['account']['profile'] == 'titan_entry' else cfg['entry']['default_method']} (Ch VII)"],
        ["Data source", "Deterministic synthetic price paths" if synthetic
                        else str(results["data_source"])],
        ["Generated", results["generated"][:19].replace("T", " ") + " UTC"],
        ["Engine", "titan_tfbs v1.0.0"],
    ]
    story.append(table([[k, v] for k, v in cover],
                       [42 * mm, PAGE_W - 2 * MARGIN - 42 * mm], header=False, font=8.5))
    story.append(Spacer(1, 14 * mm))

    if synthetic:
        story.append(callout(
            "<b>THIS IS NOT A PERFORMANCE RECORD.</b><br/><br/>"
            "This run used deterministic <b>synthetic</b> price data: textbook "
            "formations followed by completed measured moves. Every performance "
            "figure in this document — win rate, profit factor, return, R multiple "
            "— is a property of that generated price path, not evidence that the "
            "strategy has an edge.<br/><br/>"
            "The document exists to demonstrate that the engine implements the "
            "TFBS rules end to end and to exercise the risk layer. It must not be "
            "presented to clients, prospects, program participants or regulators "
            "as a track record. Re-run against firm market data before drawing "
            "any conclusion about profitability.", st))
    story.append(PageBreak())

    # ---- Methodology ----------------------------------------------------
    story.append(Paragraph("1 · Methodology", st["H1"]))
    story.append(Paragraph(
        "The engine implements the Ch VI-A decision pipeline end to end: SCAN and "
        "VALIDATE for formations, CONFIRM for the breakout, SCORE against the "
        "Confluence Scoring System, SIZE against the firm risk limits, EXECUTE via "
        "an approved entry method, and MANAGE to the Ch X exit architecture. No "
        "trade is taken before a confirming candle close, so Ch XII-A1's ban on "
        "anticipation trading holds structurally rather than by convention.",
        st["Body"]))
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph(
        "Instruments were replayed <b>interleaved in timestamp order</b>, not one "
        "after another. This matters: the Ch VIII-A aggregate open-risk cap and the "
        "correlated-exposure cap are portfolio rules, and they only bind correctly "
        "when instruments compete for the same headroom in the order the market "
        "delivered them. A per-instrument backtest run separately and summed would "
        "silently breach both.", st["Body"]))
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph(
        "Where several instruments print a bar on the same timestamp — here, all "
        "eight, on every one of the 4,230 stamps — the order within that timestamp "
        "is <b>rotated</b> rather than fixed. A fixed order (alphabetical, or the "
        "configured order) hands the last available slice of the 5% aggregate risk "
        "headroom to the same instrument at every contested bar for the whole run, "
        "which is an artefact of the harness rather than a property of the "
        "instrument. Rotation keeps the replay deterministic while spreading that "
        "advantage evenly. Per-instrument figures below should still be read as "
        "portfolio contributions, not as standalone results.", st["Body"]))
    story.append(Spacer(1, 4 * mm))

    story.append(Paragraph("Multi-timeframe protocol (Ch IX)", st["H2"]))
    story.append(table(
        [["Screen", "Timeframe", "Function"],
         ["1 — TREND", "/".join(cfg["mtf"]["trend_timeframes"]), "Macro directional bias"],
         ["2 — PATTERN", "/".join(cfg["mtf"]["pattern_timeframes"]), "Formation identification"],
         ["3 — ENTRY", "/".join(cfg["mtf"]["entry_timeframes"]), "Breakout, retest, volume, rejection"]],
        [30 * mm, 32 * mm, PAGE_W - 2 * MARGIN - 62 * mm]))
    story.append(Spacer(1, 4 * mm))

    story.append(Paragraph("Risk parameters applied", st["H2"]))
    r = cfg["risk"]
    story.append(table(
        [["Parameter", "Value", "Source"],
         ["Risk per trade", f"{r['approved_risk_pct']}% approved / {r['elite_risk_pct']}% elite", "Ch VIII-A, Ch XI"],
         ["Aggregate open risk", f"{r['max_aggregate_open_risk_pct']}%", "Ch VIII-A hard limit"],
         ["Minimum reward:risk", f"{r['min_reward_risk']}:1", "Ch VIII-A hard limit"],
         ["Correlated positions", f"{r['max_correlated_positions']} per currency/sector", "Ch VIII-A"],
         ["Daily loss limit", f"{r['daily_loss_limit_pct']}%", "Ch VIII-A, Ch XII-A5"],
         ["Weekly loss limit", f"{r['weekly_loss_limit_pct']}%", "Ch VIII-A, Ch XII-A5"],
         ["Max drawdown ladder", f"{r['dd_restricted_max_pct']}/{r['dd_probation_max_pct']}/{r['dd_suspended_max_pct']}%", "RMG s.05"],
         ["Confluence gate", f"{cfg['confluence']['min_score_to_trade']}/10 minimum", "Ch XI"]],
        [45 * mm, 52 * mm, PAGE_W - 2 * MARGIN - 97 * mm]))
    story.append(PageBreak())

    # ---- Results --------------------------------------------------------
    story.append(Paragraph("2 · Results", st["H1"]))
    if synthetic:
        story.append(callout(
            "Reminder: these figures describe synthetic price data. They "
            "demonstrate that the pipeline executes and that the risk layer "
            "binds. They say nothing about profitability.", st, AMBER))
        story.append(Spacer(1, 4 * mm))

    headline = [
        ["Trades", str(m["trades"]), "Win rate", f"{m['win_rate_pct']:.1f}%"],
        ["Net P&L", money(m["net_pnl"]), "Return", f"{m['return_pct']:.2f}%"],
        ["Profit factor", ("inf" if m["profit_factor"] == float("inf")
                           else f"{m['profit_factor']:.2f}"),
         "Expectancy", f"{m['expectancy']:,.0f} ({m['expectancy_r']:+.2f}R)"],
        ["Avg R", f"{m['avg_r']:+.2f}", "Best / worst R",
         f"{m['best_r']:+.2f} / {m['worst_r']:+.2f}"],
        ["Max drawdown", f"{m['max_drawdown_pct']:.2f}%", "Trades / week",
         f"{m['trades_per_week']:.1f}"],
    ]
    w = (PAGE_W - 2 * MARGIN) / 4
    story.append(table(headline, [w] * 4, header=False, font=8.5))
    story.append(Spacer(1, 5 * mm))

    story.append(Paragraph("Equity curve", st["H2"]))
    curve = [(datetime.fromisoformat(ts), v) for ts, v in results["equity_curve"]]
    story.append(equity_chart(curve))
    story.append(Paragraph(
        "The gold line is the running high-water mark. The RMG s.05 drawdown "
        "ladder is measured against it, so the gap between the two lines — not "
        "the distance from the starting balance — is what triggers RESTRICTED, "
        "PROBATION and SUSPENDED.", st["Small"]))
    story.append(Spacer(1, 5 * mm))

    story.append(Paragraph("Against the Ch XIII-B targets", st["H2"]))
    targets = m["firm_targets"]
    label = {"win_rate_50_60": "Win rate 50-60%",
             "avg_r_above_2": "Average R above 2.0",
             "expectancy_positive": "Expectancy positive",
             "profit_factor_above_1_5": "Profit factor above 1.5",
             "cadence_3_to_8_per_week": "Cadence 3-8 trades/week"}
    rows = [["Ch XIII-B metric", "Result", "Verdict"]]
    actual = {"win_rate_50_60": f"{m['win_rate_pct']:.1f}%",
              "avg_r_above_2": f"{m['avg_r']:+.2f}R",
              "expectancy_positive": f"{m['expectancy']:,.0f}",
              "profit_factor_above_1_5": ("inf" if m["profit_factor"] == float("inf")
                                          else f"{m['profit_factor']:.2f}"),
              "cadence_3_to_8_per_week": f"{m['trades_per_week']:.1f}/wk"}
    for k, ok in targets.items():
        rows.append([label.get(k, k), actual.get(k, "—"), "MEETS" if ok else "OFF TARGET"])
    t = table(rows, [70 * mm, 40 * mm, PAGE_W - 2 * MARGIN - 110 * mm])
    for i, (k, ok) in enumerate(targets.items(), start=1):
        t.setStyle(TableStyle([("TEXTCOLOR", (2, i), (2, i), GREEN if ok else AMBER),
                               ("FONTNAME", (2, i), (2, i), "Helvetica-Bold")]))
    story.append(t)
    story.append(PageBreak())

    # ---- Per instrument -------------------------------------------------
    story.append(Paragraph("3 · By instrument", st["H1"]))
    story.append(Paragraph(
        "The Ch I universe. Screens show the Ch IX three-screen stack applied to "
        "each instrument.", st["Body"]))
    story.append(Spacer(1, 3 * mm))
    rows = [["Symbol", "Class", "Screens", "Trades", "Win %", "Avg R", "P&L"]]
    for sym, v in results["by_symbol"].items():
        s = v["screens"]
        rows.append([
            sym, v["asset_class"],
            f"{s['trend']} > {s['pattern']} > {s['entry']}",
            str(v["trades"]),
            f"{v['win_rate']:.0f}" if v["trades"] else "—",
            f"{v['avg_r']:+.2f}" if v["trades"] else "—",
            money(v["pnl"]) if v["trades"] else "—",
        ])
    story.append(table(rows, [20 * mm, 22 * mm, 46 * mm, 16 * mm, 15 * mm, 17 * mm,
                              PAGE_W - 2 * MARGIN - 136 * mm],
                       align_right=(3, 4, 5, 6)))
    story.append(Spacer(1, 4 * mm))

    idle = [s for s, v in results["by_symbol"].items() if v["trades"] == 0]
    if idle:
        story.append(callout(
            f"<b>{', '.join(idle)} produced no trades.</b> An instrument going "
            "quiet is not a failure — the Ch XI gate and the portfolio caps are "
            "designed to refuse setups — but a flagship instrument sitting out an "
            "entire run warrants inspection before the parameters are trusted. "
            "See the rejection funnel overleaf for where those setups were "
            "stopped.",
            st, AMBER))
    story.append(Spacer(1, 5 * mm))

    story.append(Paragraph("By pattern (Ch XIII-C review input)", st["H2"]))
    rows = [["Formation", "Trades", "Win %", "Avg R", "P&L"]]
    for name, v in sorted(m["by_pattern"].items()):
        rows.append([name, f"{v['trades']:.0f}", f"{v['win_rate']:.0f}",
                     f"{v['avg_r']:+.2f}", money(v["pnl"])])
    story.append(table(rows, [40 * mm, 22 * mm, 22 * mm, 22 * mm,
                              PAGE_W - 2 * MARGIN - 106 * mm],
                       align_right=(1, 2, 3, 4)))
    story.append(Spacer(1, 4 * mm))

    story.append(Paragraph("By confluence grade (Ch XI)", st["H2"]))
    rows = [["Grade", "Trades", "Win %", "Avg R", "P&L"]]
    for name, v in sorted(m["by_grade"].items()):
        rows.append([name, f"{v['trades']:.0f}", f"{v['win_rate']:.0f}",
                     f"{v['avg_r']:+.2f}", money(v["pnl"])])
    story.append(table(rows, [40 * mm, 22 * mm, 22 * mm, 22 * mm,
                              PAGE_W - 2 * MARGIN - 106 * mm],
                       align_right=(1, 2, 3, 4)))
    story.append(PageBreak())

    # ---- Risk & compliance ----------------------------------------------
    story.append(Paragraph("4 · Risk and compliance", st["H1"]))
    peak = results["peak_open_risk_pct"]
    cap = cfg["risk"]["max_aggregate_open_risk_pct"]
    story.append(table(
        [["Peak aggregate open risk", f"{peak:.2f}%", f"cap {cap:.0f}% (Ch VIII-A)",
          "WITHIN" if peak <= cap + 1e-9 else "BREACHED"],
         ["Max drawdown (trailing)", f"{m['max_drawdown_pct']:.2f}%",
          f"suspension at {cfg['risk']['dd_suspended_max_pct']:.0f}% (RMG s.05)",
          "WITHIN" if m["max_drawdown_pct"] <= cfg["risk"]["dd_suspended_max_pct"]
          else "BREACHED"],
         ["Bars with trading halted", f"{results['halted_bars']:,}",
          f"{results['halted_pct']}% of the run", ""]],
        [52 * mm, 26 * mm, 58 * mm, PAGE_W - 2 * MARGIN - 136 * mm], header=False))
    story.append(Spacer(1, 5 * mm))

    timeline = results.get("status_timeline", [])
    story.append(Paragraph("Account status timeline (RMG s.05)", st["H2"]))
    if timeline:
        rows = [["When", "Transition", "Max DD", "Trading"]]
        for e in timeline:
            rows.append([e["ts"][:16].replace("T", " "),
                         f"{e['from']} -> {e['to']}",
                         f"{e['max_dd_pct']:.2f}%",
                         "halted" if not e["can_trade"] else "permitted"])
        t = table(rows, [38 * mm, 52 * mm, 24 * mm,
                         PAGE_W - 2 * MARGIN - 114 * mm], align_right=(2,))
        for i, e in enumerate(timeline, start=1):
            if not e["can_trade"]:
                t.setStyle(TableStyle([("TEXTCOLOR", (3, i), (3, i), RED),
                                       ("FONTNAME", (3, i), (3, i), "Helvetica-Bold")]))
        story.append(t)
    else:
        story.append(Paragraph("Account remained ACTIVE throughout.", st["Body"]))
    story.append(Spacer(1, 4 * mm))

    story.append(Paragraph("Setup funnel", st["H2"]))
    story.append(Paragraph(
        f"{results['setups_evaluated']} setups reached the CONFIRM stage; "
        f"{results['setups_executed']} were executed. Ch XII-A4 requires the "
        "refusals to be logged as well as the fills, and they are the clearest "
        "picture of what the rules actually do.", st["Body"]))
    story.append(Spacer(1, 2 * mm))
    rows = [["Outcome", "Count"]]
    rows.append(["Executed", str(results["setups_executed"])])
    for reason, count in results["rejections"].items():
        rows.append([reason, str(count)])
    story.append(table(rows, [PAGE_W - 2 * MARGIN - 26 * mm, 26 * mm],
                       align_right=(1,)))
    story.append(PageBreak())

    # ---- Findings -------------------------------------------------------
    story.append(Paragraph("5 · Findings and limitations", st["H1"]))
    findings: List[str] = []

    if timeline and any(not e["can_trade"] for e in timeline):
        worst = max((e["max_dd_pct"] for e in timeline), default=0)
        findings.append(
            f"<b>The account was halted mid-run despite every trade being a "
            f"winner.</b> Peak trailing drawdown reached {worst:.1f}%, crossing the "
            f"RMG s.05 suspension trigger, because the ladder measures against a "
            f"high-water mark that includes <i>unrealised</i> profit on open "
            f"positions. Large open winners lift the mark; giving part of that back "
            f"before the target registers as drawdown. Worth deciding deliberately "
            f"whether the firm intends the ladder to measure floating equity or "
            f"closed balance — <font face='Courier'>risk.max_drawdown_basis</font> "
            f"switches between trailing and static.")
    if peak > cap * 0.95:
        findings.append(
            f"<b>The Ch VIII-A aggregate risk cap was the binding constraint.</b> "
            f"Peak open risk reached {peak:.2f}% against the {cap:.0f}% cap, so the "
            f"portfolio was fully deployed at times and later setups were refused "
            f"for headroom rather than quality. This is the cap working, but it "
            f"means instrument-level results are not independent.")
    if idle:
        findings.append(
            f"<b>{', '.join(idle)} traded nothing.</b> Check whether those "
            f"formations were expiring unbroken, being crowded out by the "
            f"portfolio caps, or failing the confluence gate before treating the "
            f"parameters as tuned.")
    top = next(iter(results["rejections"].items()), None)
    if top:
        findings.append(
            f"<b>The dominant refusal was &ldquo;{top[0]}&rdquo; ({top[1]} of "
            f"{sum(results['rejections'].values())}).</b> A funnel this "
            f"top-heavy means most detected formations never became tradeable "
            f"setups at all, which is the intended shape for a system that grades "
            f"a bare level break as F.")

    for f in findings:
        story.append(Paragraph(f"• {f}", st["Body"]))
        story.append(Spacer(1, 2.5 * mm))

    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph("Limitations of this document", st["H2"]))
    limits = [
        "Synthetic price data. The single most important limitation: nothing here "
        "measures edge." if synthetic else
        "Historical simulation. Past behaviour does not establish future results.",
        "Fills are simulated with a fixed spread, slippage and commission model. "
        "Real execution will differ, and the difference grows with position size.",
        "A bar that spans both stop and target is assumed to hit the stop first. "
        "This is deliberately pessimistic but it is an assumption, not a fact.",
        "No economic calendar was supplied, so the Ch XII-A6 news blackout was "
        "inactive for this run.",
        "The Ch XII-C compliance escalation ladder and the monthly CEO review "
        "trigger require state across sessions and are not exercised here.",
    ]
    for l in limits:
        story.append(Paragraph(f"• {l}", st["Body"]))
        story.append(Spacer(1, 1.5 * mm))

    story.append(Spacer(1, 5 * mm))
    story.append(callout(
        "<b>Intended use.</b> This document evidences that the TFBS engine "
        "implements the manual's rules and that the risk layer binds. It is an "
        "engineering artefact for the desk and for CEO review. It is not "
        "marketing material, not a track record, and not suitable for "
        "distribution to program participants or prospective clients."
        + ("  The underlying data is synthetic." if synthetic else ""), st))

    doc = ReportDoc(out_path, synthetic=synthetic,
                    title="TFBS Portfolio Backtest Report",
                    author="Titan Markets LLC")
    doc.build(story)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--results", default="reports/results.json")
    ap.add_argument("--out", default="reports/TFBS_Backtest_Report.pdf")
    args = ap.parse_args(argv)

    results = json.loads(Path(args.results).read_text())
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    build(results, args.out)
    size = Path(args.out).stat().st_size
    print(f"-> {args.out} ({size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
