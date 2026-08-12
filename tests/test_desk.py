"""The Ch IX research desk — standing market analysis in the live bot.

The brief is only worth printing if it is true. These tests hold it to the
engine: the formations it lists are the ones the tracker is carrying, the
headroom it quotes is the headroom the risk gate will apply, and the "waiting
for" line does not claim a setup is blocked when the engine would take it.
"""

from datetime import datetime, timedelta, timezone

import pytest

from titan_tfbs.bot import TFBSBot
from titan_tfbs.config import TitanConfig
from titan_tfbs.data.synthetic import head_and_shoulders
from titan_tfbs.live import interleave, load_candles
from titan_tfbs.strategy.breakout import ARMED, BROKEN, READY, RETESTED
from titan_tfbs.strategy.desk import DeskBrief, build_brief

UTC = timezone.utc
START = datetime(2026, 1, 5, tzinfo=UTC)


def _bot(tmp_path, symbols=("GC",)):
    cfg = TitanConfig()
    cfg.mtf.min_bias_bars = 20
    cfg.journal.directory = str(tmp_path / "journal")
    return TFBSBot(cfg, symbols=list(symbols), start_time=START)


@pytest.fixture
def traded(tmp_path):
    bot = _bot(tmp_path)
    bot.on_candles("GC", head_and_shoulders(START, 2650.0, 12, 55.0))
    return bot


class TestTheBriefMatchesTheEngine:
    def test_it_lists_exactly_the_formations_being_carried(self, traded):
        brief = traded.brief()
        watching = traded.strategy.states["GC"].tracker.active
        listed = brief.symbols[0].formations
        assert len(listed) == len(watching)
        assert {w.state for w in listed} == {w.state for w in watching}
        assert {w.label for w in listed} == {
            w.pattern.type.value for w in watching
        }

    def test_headroom_is_the_gate_the_risk_manager_will_apply(self, traded):
        brief = traded.brief()
        assert brief.open_risk_pct == pytest.approx(
            traded.risk.aggregate_open_risk_pct, abs=1e-3
        )
        assert brief.risk_cap_pct == traded.config.risk.max_aggregate_open_risk_pct
        assert brief.headroom_pct == pytest.approx(
            brief.risk_cap_pct - brief.open_risk_pct, abs=1e-3
        )

    def test_the_book_line_uses_the_position_own_r(self, traded):
        brief = traded.brief()
        open_now = traded.broker.open_positions()
        if not open_now:
            pytest.skip("no open position in this scenario")
        position = open_now[0]
        price = traded.last_price["GC"]
        assert f"{position.r_at(price):+.2f}R" in brief.symbols[0].book

    def test_account_status_is_the_live_verdict(self, traded):
        brief = traded.brief()
        verdict = traded.risk.limits()
        assert brief.status == verdict.status.value
        assert brief.can_trade == verdict.can_trade


class TestTheBriefDoesNotMislead:
    def test_a_neutral_screen_1_is_not_reported_as_a_blocker(self, traded):
        """A bias-less Screen 1 costs a confluence point; it blocks nothing.

        Ch IX scores an absent bias as neutral (1/2 on the Ch XI HTF factor)
        and the Appendix A counter-trend item passes, so a brief that said
        "nothing is tradeable" would contradict the engine — which trades in
        exactly this state.
        """
        brief = traded.brief()
        symbol = brief.symbols[0]
        if symbol.screens:
            pytest.skip("this scenario did build a Screen 1 bias")
        text = "\n".join(symbol.render())
        assert "nothing is blocked" in text
        assert "nothing is tradeable" not in text

    def test_waiting_line_names_the_nearest_trigger(self, traded):
        brief = traded.brief()
        symbol = brief.symbols[0]
        if not symbol.formations:
            pytest.skip("nothing on the watchlist")
        if not brief.can_trade:
            # An account that cannot open risk is not "waiting" for a trigger;
            # the halt is the answer, and it outranks the watchlist.
            assert "cannot open risk" in symbol.waiting_for
            return
        nearest = min(symbol.formations, key=lambda w: w.distance_atr)
        assert nearest.label in symbol.waiting_for

    def test_a_halted_account_says_so_before_anything_else(self, tmp_path):
        bot = _bot(tmp_path)
        bot.on_candles("GC", head_and_shoulders(START, 2650.0, 12, 55.0))
        # Force the RMG s.05 ladder past its suspension trigger.
        bot.risk.mark_equity(bot.risk.state.peak_equity * 0.80)
        brief = bot.brief()
        assert not brief.can_trade
        assert "cannot open risk" in brief.symbols[0].waiting_for
        assert "TRADING HALTED" in brief.render()

    def test_formations_are_ordered_nearest_trigger_first(self, traded):
        symbol = traded.brief().symbols[0]
        distances = [w.distance_atr for w in symbol.formations]
        assert distances == sorted(distances)


class TestRendering:
    def test_a_blank_board_is_not_printed(self, tmp_path):
        """has_content gates the console output during warmup."""
        bot = _bot(tmp_path)
        assert not bot.brief().has_content

    def test_the_brief_renders_every_symbol(self, tmp_path):
        bot = _bot(tmp_path, symbols=("GC", "NQ"))
        bot.on_candles("GC", head_and_shoulders(START, 2650.0, 12, 55.0))
        text = bot.brief().render()
        assert "GC" in text and "NQ" in text
        assert "Ch VIII-A cap" in text

    def test_dict_carries_the_same_state(self, traded):
        brief = traded.brief()
        data = brief.to_dict()
        assert data["status"] == brief.status
        assert len(data["symbols"]) == len(brief.symbols)
        assert data["symbols"][0]["bias"] == brief.symbols[0].bias


class TestWatchlistExpiry:
    """Ch VI-B: a formation cannot sit on the watchlist forever."""

    def test_no_watch_outlives_its_window(self, tmp_path):
        """READY had no expiry branch, so a declined trigger never died.

        The stale watch could not produce a trade — the strategy only acts on
        transitions — but it stayed on the Ch VI-B watchlist, and every brief
        and snapshot reported a formation that was long dead.
        """
        cfg = TitanConfig()
        cfg.mtf.min_bias_bars = 20
        cfg.journal.directory = str(tmp_path / "journal")
        candles = load_candles(["GC"])
        bot = TFBSBot(
            cfg, symbols=["GC"], start_time=min(c[0].ts for c in candles.values())
        )
        for _, symbol, candle in interleave(candles):
            bot.on_candle(symbol, candle)

        window = cfg.breakout.retest_max_bars
        for watch in bot.strategy.states["GC"].tracker.active:
            if watch.state in (BROKEN, RETESTED, READY):
                assert watch.bars_since_break <= window, (
                    f"{watch.pattern.type.value} sat in {watch.state} for "
                    f"{watch.bars_since_break} bars against a {window}-bar window"
                )
            else:
                assert watch.bars_armed <= cfg.breakout.pattern_expiry_bars


class TestInterleaving:
    """The replay order the Ch VIII-A portfolio caps are decided in."""

    def test_every_bar_is_replayed_exactly_once(self):
        candles = load_candles(["GC", "NQ"])
        stream = interleave(candles)
        assert len(stream) == sum(len(c) for c in candles.values())
        for symbol, bars in candles.items():
            replayed = [row for row in stream if row[1] == symbol]
            assert [row[2].ts for row in replayed] == [b.ts for b in bars]

    def test_timestamps_never_go_backwards(self):
        stream = interleave(load_candles(["GC", "NQ"]))
        stamps = [row[0] for row in stream]
        assert stamps == sorted(stamps)

    def test_no_symbol_is_always_last_at_a_contested_bar(self):
        """The bug this replaced: a fixed tie-break starves one instrument.

        Whoever is evaluated last at a contested timestamp is the one refused
        for lack of Ch VIII-A headroom, so a fixed order applies that penalty
        to the same symbol at every single bar.
        """
        candles = load_candles(["GC", "NQ", "ES"])
        stream = interleave(candles)
        last_at_stamp: dict = {}
        for ts, symbol, _ in stream:
            last_at_stamp[ts] = symbol
        losers = set(last_at_stamp.values())
        assert losers == set(candles), (
            f"only {losers} ever went last — the tie-break is not rotating"
        )
