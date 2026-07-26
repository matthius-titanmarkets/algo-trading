"""Appendix A — the TFBS Pre-Trade Checklist (TFBS Ch XV).

    "Complete this checklist for every setup before execution.
     [M] = Mandatory, [P] = Preferred."                     — TFBS Appendix A

Ch XII-A3 makes the checklist a compliance requirement: *"Pre-Trade Checklist
Required: Every trade must complete the TFBS checklist before execution."*  A
setup with any failed mandatory item is not executable, regardless of its
confluence score.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from titan_tfbs.config import EntryMethod, TitanConfig
from titan_tfbs.strategy.mtf import MTFAlignment
from titan_tfbs.strategy.signals import Direction, TradeSignal


@dataclass(frozen=True)
class ChecklistItem:
    section: str
    text: str
    mandatory: bool
    passed: bool
    detail: str = ""

    @property
    def marker(self) -> str:
        return "[M]" if self.mandatory else "[P]"

    @property
    def blocking(self) -> bool:
        return self.mandatory and not self.passed


@dataclass
class ChecklistResult:
    items: List[ChecklistItem] = field(default_factory=list)

    @property
    def failures(self) -> List[ChecklistItem]:
        return [i for i in self.items if i.blocking]

    @property
    def approved(self) -> bool:
        return not self.failures

    @property
    def preferred_missed(self) -> List[ChecklistItem]:
        return [i for i in self.items if not i.mandatory and not i.passed]

    def reason(self) -> str:
        return "; ".join(f"{i.text} ({i.detail})" for i in self.failures)

    def render(self) -> str:  # pragma: no cover - reporting
        lines: List[str] = []
        section = None
        for item in self.items:
            if item.section != section:
                section = item.section
                lines.append(f"\n{section}")
            mark = "PASS" if item.passed else ("FAIL" if item.mandatory else "miss")
            lines.append(f"  {item.marker} [{mark}] {item.text} — {item.detail}")
        verdict = "APPROVED" if self.approved else "BLOCKED"
        lines.append(f"\nCHECKLIST: {verdict}")
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, object]:
        return {
            "approved": self.approved,
            "failures": [i.text for i in self.failures],
            "preferred_missed": [i.text for i in self.preferred_missed],
            "items": [
                {
                    "section": i.section,
                    "text": i.text,
                    "mandatory": i.mandatory,
                    "passed": i.passed,
                    "detail": i.detail,
                }
                for i in self.items
            ],
        }


def build_checklist(
    signal: TradeSignal,
    config: TitanConfig,
    alignment: MTFAlignment,
    *,
    news_clear: bool,
    news_detail: str,
    aggregate_risk_pct: float,
    risk_pct: float,
    journal_enabled: bool,
    pattern_timeframes: Optional[List[str]] = None,
    entry_timeframes: Optional[List[str]] = None,
) -> ChecklistResult:
    """Run Appendix A against a fully-formed signal."""
    r = ChecklistResult()
    p = signal.pattern
    b = signal.breakout
    cfg = config
    add = r.items.append
    # Screen 2 / Screen 3 timeframes come from the instrument's Ch I profile
    # when one is supplied, else from the global Ch IX defaults.
    pattern_tfs = pattern_timeframes or cfg.mtf.pattern_timeframes
    entry_tfs = entry_timeframes or cfg.mtf.entry_timeframes

    # ---- PATTERN IDENTIFICATION -----------------------------------------
    add(
        ChecklistItem(
            "PATTERN IDENTIFICATION",
            "Is a H&S, DT/DB, or Triple Top/Bottom clearly visible?",
            True,
            p.is_valid,
            f"{p.type.value} on {p.timeframe}, quality {p.quality_points}/2",
        )
    )
    prior = next((f for f in p.filters if f.name == "prior_trend"), None)
    add(
        ChecklistItem(
            "PATTERN IDENTIFICATION",
            "Does the pattern follow an established prior trend?",
            True,
            bool(prior and prior.passed),
            prior.detail if prior else "not evaluated",
        )
    )
    add(
        ChecklistItem(
            "PATTERN IDENTIFICATION",
            "Do quality filters pass? (Symmetry, spacing, volume profile)",
            True,
            not p.mandatory_failures,
            f"{p.preferred_passed}/{p.preferred_total} preferred filters met",
        )
    )

    # ---- BREAKOUT CONFIRMATION ------------------------------------------
    add(
        ChecklistItem(
            "BREAKOUT CONFIRMATION",
            "Has the neckline / confirmation line been broken by a CANDLE CLOSE?",
            True,
            not b.wick_only,
            f"{b.timeframe} close {b.close:.5f} vs level {b.level:.5f}",
        )
    )
    add(
        ChecklistItem(
            "BREAKOUT CONFIRMATION",
            "Is breakout volume > 1.5x the 20-period average?",
            False,
            b.volume_surge,
            (
                f"{b.volume_ratio:.2f}x average"
                if b.volume_ratio is not None
                else "no volume data on this feed"
            ),
        )
    )
    retest_required = signal.entry_method in (
        EntryMethod.B_STANDARD,
        EntryMethod.C_CONSERVATIVE,
    )
    add(
        ChecklistItem(
            "BREAKOUT CONFIRMATION",
            "Has a retest occurred? (Required for Method B/C entries)",
            retest_required,
            signal.retest is not None,
            (
                f"{signal.retest.rejection_kind} rejection at {signal.retest.wick_price:.5f}"
                if signal.retest
                else "no retest — Method A entry"
            ),
        )
    )

    # ---- RISK & REWARD ---------------------------------------------------
    structural_ok = _stop_is_structural(signal)
    add(
        ChecklistItem(
            "RISK & REWARD",
            "Is stop-loss at the structural invalidation point?",
            True,
            structural_ok,
            f"stop {signal.stop_loss:.5f} vs invalidation "
            f"{p.structural_invalidation:.5f}",
        )
    )
    max_risk = (
        cfg.risk.titan_entry_max_risk_pct
        if cfg.is_titan_entry()
        else cfg.risk.max_risk_per_trade_pct
    )
    add(
        ChecklistItem(
            "RISK & REWARD",
            "Is risk limited to 1-2% of account (0.5% for Titan Entry)?",
            True,
            0 < risk_pct <= max_risk + 1e-9,
            f"{risk_pct:.2f}% of account (cap {max_risk:.2f}%)",
        )
    )
    add(
        ChecklistItem(
            "RISK & REWARD",
            "Does measured move provide minimum 2:1 R:R?",
            True,
            signal.risk_reward >= cfg.risk.min_reward_risk - 1e-9,
            f"{signal.risk_reward:.2f}:1 to TP1 (min {cfg.risk.min_reward_risk:.0f}:1)",
        )
    )
    add(
        ChecklistItem(
            "RISK & REWARD",
            "Is path to TP1 clear of major S/R obstacles?",
            False,
            signal.score.clean_path > 0,
            signal.score.reasons.get("clean_path", ""),
        )
    )

    # ---- TIMEFRAME ALIGNMENT --------------------------------------------
    # Appendix A makes Screen 1 alignment MANDATORY; Ch XI's 0-point
    # "counter-trend" case is exactly what this item blocks.
    add(
        ChecklistItem(
            "TIMEFRAME ALIGNMENT",
            "Is Screen 1 (Daily/Weekly) aligned with trade direction?",
            True,
            not alignment.is_counter_trend(signal.direction),
            f"bias {alignment.bias.value} vs {signal.direction.value} "
            f"({alignment.detail})",
        )
    )
    add(
        ChecklistItem(
            "TIMEFRAME ALIGNMENT",
            "Is Screen 2 (4H/1H) showing confirmed formation?",
            True,
            p.timeframe in pattern_tfs and p.state == "confirmed",
            f"{p.type.value} confirmed on {p.timeframe}",
        )
    )
    add(
        ChecklistItem(
            "TIMEFRAME ALIGNMENT",
            "Is Screen 3 (15M/5M) providing precision entry?",
            True,
            signal.entry_timeframe in entry_tfs,
            f"trigger taken on {signal.entry_timeframe}",
        )
    )

    # ---- FINAL AUTHORIZATION --------------------------------------------
    add(
        ChecklistItem(
            "FINAL AUTHORIZATION",
            "Confluence Score = 7/10 or higher?",
            True,
            signal.score.total >= cfg.confluence.min_score_to_trade,
            f"{signal.score.total}/10 -> {signal.grade.value}",
        )
    )
    add(
        ChecklistItem(
            "FINAL AUTHORIZATION",
            "No high-impact news within 30 minutes?",
            True,
            news_clear,
            news_detail,
        )
    )
    add(
        ChecklistItem(
            "FINAL AUTHORIZATION",
            "Aggregate portfolio risk under 5%?",
            True,
            aggregate_risk_pct + risk_pct <= cfg.risk.max_aggregate_open_risk_pct + 1e-9,
            f"{aggregate_risk_pct:.2f}% open + {risk_pct:.2f}% new "
            f"(cap {cfg.risk.max_aggregate_open_risk_pct:.0f}%)",
        )
    )
    add(
        ChecklistItem(
            "FINAL AUTHORIZATION",
            "Trade logged in journal with annotated screenshot?",
            cfg.compliance.require_journal,
            journal_enabled,
            "journal writer active" if journal_enabled else "journal disabled",
        )
    )

    return r


def _stop_is_structural(signal: TradeSignal) -> bool:
    """The stop must invalidate the thesis, not merely cap the loss.

    Accepted placements (Ch VII / Ch X-A):

    * beyond the formation's own invalidation level (Method A), or
    * beyond the retest wick, which is where the structural flip fails
      (Methods B and C).
    """
    p = signal.pattern
    if signal.is_long:
        if signal.stop_loss <= p.structural_invalidation:
            return True
        return signal.retest is not None and signal.stop_loss <= signal.retest.wick_price
    if signal.stop_loss >= p.structural_invalidation:
        return True
    return signal.retest is not None and signal.stop_loss >= signal.retest.wick_price
