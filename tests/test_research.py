"""The Ch XIII-A research note — is the write-up true to the decision?

The note's value depends entirely on it being derived from the objects the
engine decided on rather than being a second, parallel description of the
trade. These tests hold it to that: every number it states is checked against
the object it claims to be reporting, and the note is required to be honest
about what is *weak* in a setup, not only what qualified it.
"""

from datetime import datetime, timedelta, timezone

import pytest

from titan_tfbs.bot import TFBSBot
from titan_tfbs.config import EntryMethod, TitanConfig
from titan_tfbs.core.candles import Candle
from titan_tfbs.core.structure import Line, Pivot, PivotType, Trend
from titan_tfbs.data.synthetic import head_and_shoulders
from titan_tfbs.instruments import get_instrument
from titan_tfbs.patterns.base import (
    FilterResult,
    FilterStatus,
    Pattern,
    PatternType,
)
from titan_tfbs.strategy.research import (
    PATTERN_NAMES,
    SCORE_FACTORS,
    ResearchNote,
    build_note,
)
from titan_tfbs.strategy.signals import (
    BreakoutEvent,
    ConfluenceScore,
    Direction,
    Grade,
    TradeSignal,
)

UTC = timezone.utc
START = datetime(2026, 1, 5, tzinfo=UTC)
NOW = datetime(2026, 1, 5, 12, 0, tzinfo=UTC)


def weak_signal() -> TradeSignal:
    """A setup that passes the gate but has something wrong with every screen.

    Built by hand rather than found in generated data: the generator produces
    textbook formations, so a note's honesty about flaws cannot be tested with
    one.
    """
    entry, stop, tp1 = 2600.0, 2590.0, 2622.0
    pattern = Pattern(
        type=PatternType.HEAD_SHOULDERS,
        symbol="GC",
        timeframe="4H",
        pivots=[Pivot(0, NOW, entry, PivotType.HIGH, 1000.0)],
        trigger_line=Line(0, entry, 10, entry),
        measured_height=22.0,
        structural_invalidation=2612.0,
        start_index=0,
        end_index=30,
        start_ts=NOW - timedelta(hours=30),
        end_ts=NOW,
        filters=[
            FilterResult("prior_trend", FilterStatus.MANDATORY, True, "ok"),
            FilterResult(
                "shoulder_symmetry_height", FilterStatus.PREFERRED, False,
                "31.0% apart (tolerance 20%)",
            ),
            FilterResult(
                "neckline_slope", FilterStatus.PREFERRED, False,
                "22.0deg (max 15deg)",
            ),
        ],
        quality_points=1,
        quality=0.33,
        extra_shoulders=1,
        choppy_context=True,
        prior_trend=Trend.UP,
        atr=5.0,
        state="confirmed",
    )
    pattern.set_time_anchor(NOW, 240)
    breakout = BreakoutEvent(
        pattern=pattern,
        ts=NOW,
        candle=Candle(NOW, entry, entry, entry, entry, 900),
        timeframe="15M",
        level=entry,
        close=entry - 1.0,
        atr=5.0,
        volume_ratio=0.9,
        volume_surge=False,
        divergence=True,
    )
    return TradeSignal(
        symbol="GC",
        direction=Direction.SHORT,
        pattern=pattern,
        breakout=breakout,
        entry_method=EntryMethod.A_AGGRESSIVE,
        entry_price=entry,
        stop_loss=2612.0,
        take_profit_1=2578.0,
        take_profit_2=2570.0,
        take_profit_3=2560.0,
        risk_reward=abs(tp1 - entry) / abs(entry - stop),
        score=ConfluenceScore(
            pattern_quality=1, breakout_strength=1, retest_confirm=0,
            htf_alignment=2, sr_confluence=0, rr_ratio=1, clean_path=0,
            reasons={"clean_path": "2 opposing level(s) between entry and TP1"},
        ),
        grade=Grade.APPROVED,
        created_ts=NOW,
        trend_timeframe="1D",
        pattern_timeframe="4H",
        entry_timeframe="15M",
    )


def _bot(tmp_path, **journal_overrides):
    cfg = TitanConfig()
    cfg.mtf.min_bias_bars = 20
    cfg.journal.directory = str(tmp_path / "journal")
    for key, value in journal_overrides.items():
        setattr(cfg.journal, key, value)
    return TFBSBot(cfg, symbols=["GC"], start_time=START)


def _traded(tmp_path, **journal_overrides) -> TFBSBot:
    bot = _bot(tmp_path, **journal_overrides)
    bot.on_candles("GC", head_and_shoulders(START, 2650.0, 12, 55.0))
    bot.close_all()
    return bot


@pytest.fixture(scope="module")
def note_and_signal():
    """One executed trade's note, alongside the signal it was built from."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        cfg = TitanConfig()
        cfg.mtf.min_bias_bars = 20
        cfg.journal.directory = f"{tmp}/journal"
        bot = TFBSBot(cfg, symbols=["GC"], start_time=START)
        bot.on_candles("GC", head_and_shoulders(START, 2650.0, 12, 55.0))
        bot.close_all()
        assert bot.closed_positions, "no trade to write a note about"
        signal = bot.closed_positions[0].signal
        assert signal.research is not None
        return signal.research, signal


class TestTheNoteMatchesTheDecision:
    def test_every_executed_trade_carries_a_note(self, tmp_path):
        bot = _traded(tmp_path)
        assert bot.closed_positions
        for position in bot.closed_positions:
            assert isinstance(position.signal.research, ResearchNote)

    def test_headline_states_the_actual_grade_and_score(self, note_and_signal):
        note, signal = note_and_signal
        assert signal.symbol in note.headline
        assert signal.direction.value.upper() in note.headline
        assert PATTERN_NAMES[signal.pattern.type] in note.headline
        assert f"{signal.grade.value} {signal.score.total}/10" in note.headline

    def test_confluence_section_reports_the_scored_points(self, note_and_signal):
        """The section is the scorecard, not a retelling of it."""
        note, signal = note_and_signal
        section = next(s for s in note.sections if s.heading.startswith("CONFLUENCE"))
        cfg = TitanConfig().confluence
        for (key, label, cap_attr), line in zip(SCORE_FACTORS, section.lines):
            points = getattr(signal.score, key)
            assert line.startswith(f"{points}/{getattr(cfg, cap_attr)}")
            assert label in line
            # ...and the scorer's own justification, not a fresh one.
            assert signal.score.reasons[key] in line

    def test_thesis_quotes_the_break_and_the_target(self, note_and_signal):
        note, signal = note_and_signal
        assert f"{signal.risk_reward:.2f}R" in note.thesis
        assert signal.breakout.timeframe in note.thesis
        assert signal.pattern.timeframe in note.thesis

    def test_plan_line_quotes_the_size_actually_taken(self, note_and_signal):
        note, signal = note_and_signal
        assert f"{signal.position_size:g}" in note.plan_line
        assert f"{signal.risk_pct:.2f}%" in note.plan_line

    def test_stop_is_described_by_where_it_actually_sits(self, note_and_signal):
        """Method B's stop is tighter than the formation's own invalidation.

        Claiming otherwise would tell a trader the thesis is dead when only
        the retest has failed, so the wording has to follow the geometry.
        """
        note, signal = note_and_signal
        stop_line = note.invalidation[0]
        beyond = (
            signal.stop_loss <= signal.pattern.structural_invalidation
            if signal.is_long
            else signal.stop_loss >= signal.pattern.structural_invalidation
        )
        if beyond:
            assert "beyond the formation's own invalidation" in stop_line
        else:
            assert "inside the formation's own invalidation" in stop_line


class TestTheNoteIsHonest:
    """A note that only lists strengths is marketing, not research."""

    @pytest.fixture
    def weak(self):
        return build_note(
            weak_signal(), None, TitanConfig(), instrument=get_instrument("GC")
        )

    def test_missed_preferred_filters_are_named(self, weak):
        assert any("shoulder_symmetry_height" in c for c in weak.caveats)
        assert any("neckline_slope" in c for c in weak.caveats)

    def test_a_breakout_without_volume_is_disclosed(self, weak):
        assert any("0.90x is below the Ch V-A 1.5x surge" in c for c in weak.caveats)

    def test_divergence_choppiness_and_obstacles_are_disclosed(self, weak):
        joined = " | ".join(weak.caveats)
        assert "divergence" in joined
        assert "choppy macro context" in joined
        assert "opposing level(s) between entry and TP1" in joined
        assert "extra shoulder" in joined

    def test_a_textbook_setup_is_not_padded_with_invented_flaws(self, note_and_signal):
        """The other half of honesty: no caveat without something behind it."""
        note, signal = note_and_signal
        for caveat in note.caveats:
            assert caveat.strip(), "blank caveat"
        if signal.breakout.volume_surge:
            assert not any("surge threshold" in c for c in note.caveats)

    def test_rejected_setups_can_be_written_up_too(self, tmp_path):
        """Ch XII-A4 logs skipped trades; the note explains the refusal."""
        cfg = TitanConfig()
        cfg.mtf.min_bias_bars = 20
        cfg.journal.directory = str(tmp_path / "journal")
        cfg.journal.log_research_for_skipped = True
        cfg.confluence.min_score_to_trade = 11    # refuse everything
        cfg.confluence.approved_score = 11
        cfg.confluence.elite_score = 12
        bot = TFBSBot(cfg, symbols=["GC"], start_time=START)
        bot.on_candles("GC", head_and_shoulders(START, 2650.0, 12, 55.0))

        skipped = [n for n in bot.journal.research if not n["taken"]]
        assert skipped, "no note written for a refused setup"
        assert skipped[0]["blocked_by"], "the note does not say what blocked it"


class TestTheResearchLog:
    def test_notes_are_written_to_the_markdown_log(self, tmp_path):
        bot = _traded(tmp_path)
        text = bot.journal.research_path.read_text()
        assert text.startswith("# TFBS research log")
        for position in bot.closed_positions:
            assert position.signal.research.headline in text

    def test_the_thesis_reaches_the_trade_row(self, tmp_path):
        """Ch XIII-A: the row carries the reasoning, not only the levels."""
        bot = _traded(tmp_path)
        row = bot.journal.records[0]
        assert row["research"] == bot.closed_positions[0].signal.research.thesis

    def test_skipped_setups_stay_out_of_the_log_by_default(self, tmp_path):
        bot = _traded(tmp_path)
        assert all(n["taken"] for n in bot.journal.research)

    def test_research_can_be_turned_off_entirely(self, tmp_path):
        bot = _traded(tmp_path, log_research=False)
        assert bot.closed_positions
        assert not bot.journal.research_path.exists()
        assert bot.closed_positions[0].signal.research is None


class TestRendering:
    def test_all_three_renderings_carry_the_thesis(self, note_and_signal):
        note, _ = note_and_signal
        # The thesis wraps in the console renderings, so compare on words.
        for text in (note.overview(), note.render(), note.to_markdown()):
            assert set(note.thesis.split()) <= set(text.split())

    def test_dict_round_trips_every_section(self, note_and_signal):
        note, _ = note_and_signal
        data = note.to_dict()
        assert [s["heading"] for s in data["sections"]] == [
            s.heading for s in note.sections
        ]
        assert data["caveats"] == note.caveats
        assert data["taken"] is True

    def test_prices_use_the_instrument_precision(self, note_and_signal):
        """FX needs five decimals; a futures contract does not."""
        _, signal = note_and_signal
        fx = build_note(signal, None, TitanConfig(), instrument=get_instrument("EURUSD"))
        gc = build_note(signal, None, TitanConfig(), instrument=get_instrument("GC"))
        assert f"{signal.stop_loss:,.5f}" in fx.plan_line
        assert f"{signal.stop_loss:,.2f}" in gc.plan_line
