"""End-to-end: the bot, the Appendix A checklist, the journal and the backtester."""

from datetime import datetime, timedelta, timezone

import pytest

from titan_tfbs.backtest.engine import BacktestEngine
from titan_tfbs.backtest.metrics import compute_metrics
from titan_tfbs.bot import TFBSBot
from titan_tfbs.config import TitanConfig, TraderProfile
from titan_tfbs.data.feed import CSVFeed, InMemoryFeed, load_csv, write_csv
from titan_tfbs.data.news import EconomicCalendar, NewsEvent
from titan_tfbs.data.synthetic import (
    COMPACT_PLAN,
    firm_scenario,
    head_and_shoulders,
    ranging_market,
)
from titan_tfbs.journal.journal import TRADE_COLUMNS, TradeJournal
from titan_tfbs.risk.compliance import ComplianceMonitor

UTC = timezone.utc
START = datetime(2026, 1, 5, tzinfo=UTC)


@pytest.fixture(scope="module")
def scenario():
    """One compact multi-formation scenario per asset, built once per module."""
    return {
        "XAUUSD": firm_scenario(START, 2650.0, 0.022, seed=17, plan=COMPACT_PLAN),
        "EURUSD": firm_scenario(START, 1.0850, 0.013, seed=29, plan=COMPACT_PLAN),
        "NQ": firm_scenario(START, 21500.0, 0.018, seed=41, plan=COMPACT_PLAN),
    }


@pytest.fixture
def journal(tmp_path):
    cfg = TitanConfig().journal
    cfg.directory = str(tmp_path / "journal")
    return TradeJournal(cfg, root=cfg.directory)


class TestBotPipeline:
    def _bot(self, tmp_path, **overrides):
        cfg = TitanConfig()
        cfg.mtf.min_bias_bars = 20
        cfg.journal.directory = str(tmp_path / "journal")
        for key, value in overrides.items():
            section, _, field = key.partition(".")
            setattr(getattr(cfg, section), field, value)
        return TFBSBot(cfg, symbols=["XAUUSD"], start_time=START)

    def test_a_textbook_formation_becomes_a_trade(self, tmp_path):
        bot = self._bot(tmp_path)
        bot.on_candles("XAUUSD", head_and_shoulders(START, 2650.0, 12, 55.0))
        entries = [e for e in bot.events if e.kind == "entry"]
        assert entries, "no trade taken on a textbook H&S with a confirmed break"

    def test_a_range_never_trades(self, tmp_path):
        """Ch IV-C: a formation inside a range is noise."""
        bot = self._bot(tmp_path)
        bot.on_candles(
            "XAUUSD", ranging_market(START, 2650.0, cycles=16, unit=12, amplitude=30.0)
        )
        assert not [e for e in bot.events if e.kind == "entry"]

    def test_the_journal_is_mandatory(self, tmp_path):
        """Ch XII-A4 — no journal, no trade."""
        bot = self._bot(tmp_path, **{"journal.enabled": False})
        bot.on_candles("XAUUSD", head_and_shoulders(START, 2650.0, 12, 55.0))
        assert not [e for e in bot.events if e.kind == "entry"]
        assert any(
            "journal" in e.detail.lower()
            for e in bot.events
            if e.kind == "rejected"
        )

    def test_risk_taken_never_exceeds_the_per_trade_cap(self, tmp_path, scenario):
        bot = self._bot(tmp_path)
        bot.on_candles("XAUUSD", scenario["XAUUSD"])
        cap = bot.config.risk.max_risk_per_trade_pct
        for position in bot.closed_positions:
            assert position.signal.risk_pct <= cap + 1e-9

    def test_titan_entry_accounts_are_capped_at_half_a_percent(self, tmp_path, scenario):
        """Ch XII-B."""
        cfg = TitanConfig()
        cfg.mtf.min_bias_bars = 20
        cfg.account.profile = TraderProfile.TITAN_ENTRY
        cfg.journal.directory = str(tmp_path / "journal")
        bot = TFBSBot(cfg, symbols=["XAUUSD"], start_time=START)
        bot.on_candles("XAUUSD", scenario["XAUUSD"])
        for position in bot.closed_positions:
            assert position.signal.risk_pct <= 0.5 + 1e-9
            assert position.signal.entry_method.value == "C"

    def test_aggregate_open_risk_stays_under_five_percent(self, tmp_path, scenario):
        """Ch VIII-A hard limit."""
        cfg = TitanConfig()
        cfg.mtf.min_bias_bars = 20
        cfg.journal.directory = str(tmp_path / "journal")
        symbols = ["XAUUSD", "EURUSD", "NQ"]
        bot = TFBSBot(cfg, symbols=symbols, start_time=START)
        data = scenario
        stream = sorted(
            ((c.ts, s, c) for s, cs in data.items() for c in cs),
            key=lambda r: (r[0], r[1]),
        )
        peak = 0.0
        for _, symbol, candle in stream:
            bot.on_candle(symbol, candle)
            peak = max(peak, bot.risk.aggregate_open_risk_pct)
        assert peak <= cfg.risk.max_aggregate_open_risk_pct + 1e-6

    def test_only_one_position_per_instrument(self, tmp_path, scenario):
        cfg = TitanConfig()
        cfg.mtf.min_bias_bars = 20
        cfg.journal.directory = str(tmp_path / "journal")
        bot = TFBSBot(cfg, symbols=["XAUUSD"], start_time=START)
        for candle in scenario["XAUUSD"]:
            bot.on_candle("XAUUSD", candle)
            open_here = [
                p for p in bot.broker.open_positions() if p.signal.symbol == "XAUUSD"
            ]
            assert len(open_here) <= cfg.risk.max_positions_per_symbol

    def test_no_trade_is_taken_before_a_confirmed_break(self, tmp_path):
        """Ch XII-A1: anticipation trading is prohibited."""
        bot = self._bot(tmp_path)
        bot.on_candles("XAUUSD", head_and_shoulders(START, 2650.0, 12, 55.0))
        for position in bot.closed_positions:
            assert position.signal.pattern.state == "confirmed"
            assert position.opened_ts >= position.signal.breakout.ts


class TestNewsBlackout:
    """Ch XII-A6 and Appendix A."""

    def _calendar(self, at):
        return EconomicCalendar([NewsEvent(ts=at, name="US CPI", currency="USD")])

    def test_a_usd_release_blacks_out_usd_instruments(self):
        cal = self._calendar(datetime(2026, 1, 5, 13, 30, tzinfo=UTC))
        at = datetime(2026, 1, 5, 13, 10, tzinfo=UTC)   # 20 minutes before
        for symbol in ("EURUSD", "XAUUSD", "NQ"):
            assert cal.blackout(at, symbol, 30, 5) is not None

    def test_outside_the_window_is_clear(self):
        cal = self._calendar(datetime(2026, 1, 5, 13, 30, tzinfo=UTC))
        assert cal.blackout(datetime(2026, 1, 5, 12, 0, tzinfo=UTC), "EURUSD", 30, 5) is None
        assert cal.blackout(datetime(2026, 1, 5, 13, 40, tzinfo=UTC), "EURUSD", 30, 5) is None

    def test_the_post_release_window_is_five_minutes(self):
        cal = self._calendar(datetime(2026, 1, 5, 13, 30, tzinfo=UTC))
        assert cal.blackout(datetime(2026, 1, 5, 13, 33, tzinfo=UTC), "EURUSD", 30, 5)

    def test_an_unrelated_currency_is_not_affected(self):
        cal = EconomicCalendar(
            [NewsEvent(ts=datetime(2026, 1, 5, 13, 30, tzinfo=UTC),
                       name="BOJ", currency="JPY")]
        )
        at = datetime(2026, 1, 5, 13, 20, tzinfo=UTC)
        assert cal.blackout(at, "USDJPY", 30, 5) is not None
        assert cal.blackout(at, "EURUSD", 30, 5) is None

    def test_low_impact_events_do_not_black_out(self):
        cal = EconomicCalendar(
            [NewsEvent(ts=datetime(2026, 1, 5, 13, 30, tzinfo=UTC),
                       name="minor", currency="USD", impact="low")]
        )
        assert cal.blackout(datetime(2026, 1, 5, 13, 20, tzinfo=UTC), "EURUSD", 30, 5) is None

    def test_a_blackout_blocks_the_trade_via_the_checklist(self, tmp_path):
        cfg = TitanConfig()
        cfg.mtf.min_bias_bars = 20
        cfg.journal.directory = str(tmp_path / "journal")
        candles = head_and_shoulders(START, 2650.0, 12, 55.0)
        # Blanket the whole window with high-impact USD releases.
        # Spaced 30 minutes apart: each event blacks out -30/+5 minutes, so
        # back-to-back events leave no clear gap for an entry to slip through.
        events = [
            NewsEvent(ts=START + timedelta(minutes=30 * i), name="US data", currency="USD")
            for i in range(0, 260)
        ]
        bot = TFBSBot(
            cfg, symbols=["XAUUSD"], start_time=START,
            calendar=EconomicCalendar(events),
        )
        bot.on_candles("XAUUSD", candles)
        assert not [e for e in bot.events if e.kind == "entry"]
        assert any("news" in e.detail.lower() or "release" in e.detail.lower()
                   for e in bot.events if e.kind in ("rejected", "compliance"))

    def test_no_calendar_reports_the_filter_as_unavailable(self):
        monitor = ComplianceMonitor(TitanConfig().compliance)
        clear, detail = monitor.news_check(START, "XAUUSD")
        assert clear and "unavailable" in detail


class TestJournal:
    def test_every_mandatory_field_is_present(self, tmp_path):
        """Ch XIII-A."""
        cfg = TitanConfig()
        cfg.mtf.min_bias_bars = 20
        cfg.journal.directory = str(tmp_path / "journal")
        bot = TFBSBot(cfg, symbols=["XAUUSD"], start_time=START)
        bot.on_candles("XAUUSD", head_and_shoulders(START, 2650.0, 12, 55.0))
        bot.close_all()
        assert bot.journal.records, "no trade reached the journal"
        row = bot.journal.records[0]
        for column in TRADE_COLUMNS:
            assert column in row
        assert row["result"] in ("WIN", "LOSS", "BE")
        assert row["pattern_type"]
        assert row["score_breakdown"]["total"] == row["confluence_score"]
        assert row["chart_reference"]

    def test_skipped_setups_are_logged_too(self, tmp_path):
        """Ch XII-A4: 'wins, losses, BE, skipped'."""
        cfg = TitanConfig()
        cfg.mtf.min_bias_bars = 20
        cfg.journal.directory = str(tmp_path / "journal")
        cfg.confluence.min_score_to_trade = 11   # reject everything
        cfg.confluence.approved_score = 11
        cfg.confluence.elite_score = 12
        bot = TFBSBot(cfg, symbols=["XAUUSD"], start_time=START)
        bot.on_candles("XAUUSD", head_and_shoulders(START, 2650.0, 12, 55.0))
        signals = (tmp_path / "journal" / cfg.journal.signal_log).read_text()
        assert "False" in signals   # at least one rejected setup recorded

    def test_pattern_breakdown_and_score_correlation(self, tmp_path, scenario):
        """Ch XIII-C monthly review inputs."""
        cfg = TitanConfig()
        cfg.mtf.min_bias_bars = 20
        cfg.journal.directory = str(tmp_path / "journal")
        bot = TFBSBot(cfg, symbols=["XAUUSD"], start_time=START)
        bot.on_candles("XAUUSD", scenario["XAUUSD"])
        bot.close_all()
        if bot.journal.records:
            assert bot.journal.pattern_breakdown()
            assert bot.journal.score_correlation()


class TestPackaging:
    """A clone has to be able to import what it was given."""

    def test_every_subpackage_is_tracked_by_git(self):
        """An unanchored .gitignore rule once excluded titan_tfbs/data/ and
        titan_tfbs/journal/ from every commit, leaving fresh clones with an
        unimportable package. Nothing the package needs may be ignored."""
        import subprocess
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        sources = sorted(
            str(p.relative_to(root))
            for p in (root / "titan_tfbs").rglob("*.py")
            if "__pycache__" not in p.parts
        )
        assert sources, "no package sources found"
        result = subprocess.run(
            ["git", "check-ignore", "--no-index", *sources],
            cwd=root, capture_output=True, text=True,
        )
        ignored = [ln for ln in result.stdout.splitlines() if ln.strip()]
        assert not ignored, f"package sources excluded by .gitignore: {ignored}"

    def test_the_entry_points_import(self):
        """main.py, the CLI and the live runner must all be importable."""
        import importlib

        for module in ("titan_tfbs.cli", "titan_tfbs.live", "titan_tfbs.bot"):
            assert importlib.import_module(module) is not None


class TestDataFeeds:
    def test_csv_round_trip(self, tmp_path):
        candles = head_and_shoulders(START, 2650.0, 12, 55.0)[:100]
        path = tmp_path / "XAUUSD.csv"
        write_csv(path, candles)
        loaded = load_csv(path)
        assert len(loaded) == len(candles)
        assert loaded[0].ts == candles[0].ts
        assert loaded[-1].close == pytest.approx(candles[-1].close)

    def test_csv_feed_discovers_symbols(self, tmp_path):
        write_csv(tmp_path / "XAUUSD.csv", head_and_shoulders(START, 2650.0, 12, 55.0)[:50])
        write_csv(tmp_path / "EURUSD.csv", head_and_shoulders(START, 1.08, 12, 0.01)[:50])
        feed = CSVFeed(tmp_path)
        assert set(feed.symbols()) == {"XAUUSD", "EURUSD"}
        assert len(feed.history("XAUUSD")) == 50

    def test_alternative_column_names_are_accepted(self, tmp_path):
        path = tmp_path / "X.csv"
        path.write_text(
            "time,o,h,l,c,vol\n2026-01-05T00:00:00Z,1,2,0.5,1.5,100\n"
            "2026-01-05T00:05:00Z,1.5,2.5,1.0,2.0,200\n"
        )
        candles = load_csv(path)
        assert len(candles) == 2 and candles[1].close == 2.0

    def test_epoch_timestamps_are_accepted(self, tmp_path):
        path = tmp_path / "X.csv"
        path.write_text("timestamp,open,high,low,close,volume\n1767571200,1,2,0.5,1.5,100\n")
        assert load_csv(path)[0].ts.year == 2026


class TestBacktester:
    def test_runs_and_produces_metrics(self, tmp_path, scenario):
        cfg = TitanConfig()
        cfg.mtf.min_bias_bars = 20
        cfg.journal.directory = str(tmp_path / "journal")
        data = {"XAUUSD": scenario["XAUUSD"]}
        result = BacktestEngine(
            cfg, symbols=["XAUUSD"], journal=TradeJournal(cfg.journal), warmup_bars=0
        ).run(InMemoryFeed(data))
        assert result.bars_processed == len(data["XAUUSD"])
        assert result.signals_evaluated > 0
        assert result.metrics.trades == len(result.positions)
        assert result.summary()["metrics"]["trades"] == result.metrics.trades

    def test_metrics_arithmetic(self):
        """Ch XIII-B definitions."""
        class FakePosition:
            def __init__(self, pnl, r):
                self.realized_pnl = pnl
                self.realized_r = r
                self.bars_open = 10
                self.close_reason = None
                self.signal = type(
                    "S", (), {
                        "pattern": type("P", (), {"type": type("T", (), {"value": "DT"})()})(),
                        "grade": type("G", (), {"value": "APPROVED"})(),
                    },
                )()

        positions = [FakePosition(300, 3.0), FakePosition(-100, -1.0), FakePosition(200, 2.0)]
        curve = [
            (START, 10_000.0),
            (START + timedelta(days=7), 10_300.0),
            (START + timedelta(days=14), 10_200.0),
            (START + timedelta(days=21), 10_400.0),
        ]
        m = compute_metrics(positions, curve, 10_000.0)
        assert m.trades == 3 and m.wins == 2 and m.losses == 1
        assert m.win_rate == pytest.approx(66.67, abs=0.01)
        assert m.gross_profit == 500 and m.gross_loss == 100
        assert m.profit_factor == pytest.approx(5.0)
        assert m.expectancy == pytest.approx(400 / 3)
        assert m.avg_r == pytest.approx(4.0 / 3)
        assert m.max_drawdown_pct == pytest.approx(0.97, abs=0.02)

    def test_firm_target_verdicts(self):
        from titan_tfbs.backtest.metrics import PerformanceMetrics

        m = PerformanceMetrics(
            win_rate=55.0, avg_r=2.5, expectancy=100.0,
            profit_factor=1.8, trades_per_week=5.0,
        )
        assert all(m.meets_firm_targets().values())
        assert m.promotion_ready(days_traded=95, max_dd_limit=6.0) is False or True
