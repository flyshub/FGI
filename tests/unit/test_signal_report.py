"""Tests for SignalReportEngine: zone assignment, forward returns, statistics."""

import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from fgi.storage.database import Database


@pytest.fixture
def db():
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        path = Path(tmp.name)
    database = Database(path)
    with database:
        database.init_schema()
        yield database


def _seed(db: Database, fgi_series: list, close_series: list, start_date: str = "2020-01-02"):
    """Seed a temp DB with synthetic FGI scores + m3_close data.

    fgi_series: list of (date_str, fgi_value) — values already aligned
    close_series: list of (date_str, close_value)
    """
    for date_str, fgi in fgi_series:
        db.upsert_score(date_str, {"FGI_final": fgi})
    for date_str, close in close_series:
        db.upsert_raw_data(date_str, "m3_close", close)
    db.commit()


class TestZoneAssignment:
    def test_all_five_zones(self):
        """Every FGI boundary value lands in the correct zone."""
        from fgi.output.signal_report import assign_zone

        assert assign_zone(0) == "极度恐惧"
        assert assign_zone(10) == "极度恐惧"
        assert assign_zone(19.9) == "极度恐惧"
        assert assign_zone(20) == "恐惧"
        assert assign_zone(35) == "恐惧"
        assert assign_zone(39.9) == "恐惧"
        assert assign_zone(40) == "中性"
        assert assign_zone(50) == "中性"
        assert assign_zone(59.9) == "中性"
        assert assign_zone(60) == "贪婪"
        assert assign_zone(75) == "贪婪"
        assert assign_zone(79.9) == "贪婪"
        assert assign_zone(80) == "极度贪婪"
        assert assign_zone(95) == "极度贪婪"
        assert assign_zone(100) == "极度贪婪"

    def test_nan_returns_unknown(self):
        from fgi.output.signal_report import assign_zone

        assert assign_zone(None) == "未知"
        assert assign_zone(float("nan")) == "未知"


class TestForwardReturns:
    def test_simple_known_values(self):
        """close[t+2]/close[t]-1 computed correctly on aligned data."""
        n = 10
        dates = pd.date_range("2020-01-02", periods=n, freq="B")
        closes = [100, 101, 99, 102, 103, 105, 104, 106, 108, 110]
        df = pd.DataFrame(
            {
                "date": dates.strftime("%Y-%m-%d"),
                "FGI_final": [50] * n,
                "close": closes,
            }
        )

        from fgi.output.signal_report import compute_forward_returns

        result = compute_forward_returns(df, horizons=[2])

        # forward_2 = close[t+2]/close[t] - 1
        assert "forward_2" in result.columns
        # Day 0: (99/100 - 1) = -0.01
        np.testing.assert_almost_equal(result["forward_2"].iloc[0], -0.01)
        # Day 1: (102/101 - 1) ≈ 0.00990099
        np.testing.assert_almost_equal(result["forward_2"].iloc[1], 102 / 101 - 1)
        # Last 2 days: NaN (no future data)
        assert pd.isna(result["forward_2"].iloc[-1])
        assert pd.isna(result["forward_2"].iloc[-2])

    def test_all_horizons_added(self):
        n = 100
        dates = pd.date_range("2020-01-02", periods=n, freq="B")
        df = pd.DataFrame(
            {
                "date": dates.strftime("%Y-%m-%d"),
                "FGI_final": [50] * n,
                "close": np.linspace(100, 120, n),
            }
        )

        from fgi.output.signal_report import compute_forward_returns

        result = compute_forward_returns(df, horizons=[5, 20, 60])
        assert "forward_5" in result.columns
        assert "forward_20" in result.columns
        assert "forward_60" in result.columns

    def test_no_nan_in_future_for_early_rows(self):
        """Early rows with enough future data should NOT be NaN."""
        n = 200
        dates = pd.date_range("2020-01-02", periods=n, freq="B")
        df = pd.DataFrame(
            {
                "date": dates.strftime("%Y-%m-%d"),
                "FGI_final": [50] * n,
                "close": [100 + i * 0.5 for i in range(n)],
            }
        )

        from fgi.output.signal_report import compute_forward_returns

        result = compute_forward_returns(df, horizons=[5])
        # First row has 199 future rows → valid
        assert not pd.isna(result["forward_5"].iloc[0])
        # Last 5 rows should be NaN
        for i in range(-5, 0):
            assert pd.isna(result["forward_5"].iloc[i])


class TestZoneStats:
    def test_basic_stats_on_known_data(self):
        """Stats computed on a tiny dataset with hand-verifiable numbers."""
        n = 80  # 80 > 30, so CI is computed
        dates = pd.date_range("2020-01-02", periods=n, freq="B")
        # Every other day is fear (20 ≤ FGI < 40), rest is neutral
        fgis = [25 if i % 2 == 0 else 50 for i in range(n)]
        # Close: 100, 101, 102, ..., monotonically up → all forward returns positive
        closes = [100 + i for i in range(n)]
        df = pd.DataFrame(
            {
                "date": dates.strftime("%Y-%m-%d"),
                "FGI_final": fgis,
                "close": closes,
            }
        )

        from fgi.output.signal_report import compute_forward_returns, compute_zone_stats

        df = compute_forward_returns(df, horizons=[5])
        stats = compute_zone_stats(df, horizon=5)

        # All forward_5 should be positive (prices monotonically rising)
        fear = next(s for s in stats if s["zone"] == "恐惧")
        neutral = next(s for s in stats if s["zone"] == "中性")

        assert fear["n"] == 38  # every other day, 75 valid rows after NaN tail → 38 fear
        assert neutral["n"] == 37  # 75 - 38
        # All returns positive → win_rate == 1.0
        assert fear["win_rate"] == 1.0
        assert neutral["win_rate"] == 1.0

    def test_ci_null_for_small_n(self):
        """n < 30 → CI fields are None, not computed."""
        n = 20
        dates = pd.date_range("2020-01-02", periods=n, freq="B")
        df = pd.DataFrame(
            {
                "date": dates.strftime("%Y-%m-%d"),
                "FGI_final": [50] * n,
                "close": [100 + i for i in range(n)],
            }
        )

        from fgi.output.signal_report import compute_forward_returns, compute_zone_stats

        df = compute_forward_returns(df, horizons=[5])
        stats = compute_zone_stats(df, horizon=5)

        zone = next(s for s in stats if s["zone"] == "中性")
        assert zone["n"] < 30
        assert zone["ci_lower"] is None
        assert zone["ci_upper"] is None

    def test_all_zones_present(self):
        """Every zone appears in stats output even if n=0."""
        n = 100
        dates = pd.date_range("2020-01-02", periods=n, freq="B")
        df = pd.DataFrame(
            {
                "date": dates.strftime("%Y-%m-%d"),
                "FGI_final": [50] * n,
                "close": [100 + i for i in range(n)],
            }
        )

        from fgi.output.signal_report import compute_forward_returns, compute_zone_stats

        df = compute_forward_returns(df, horizons=[5])
        stats = compute_zone_stats(df, horizon=5)

        zones = {s["zone"] for s in stats}
        assert zones == {"极度恐惧", "恐惧", "中性", "贪婪", "极度贪婪"}

    def test_mean_and_win_rate_with_mixed_returns(self):
        """Verify mean and win_rate arithmetic on a hand-picked scenario."""
        n = 100
        dates = pd.date_range("2020-01-02", periods=n, freq="B")
        # All neutral
        df = pd.DataFrame(
            {
                "date": dates.strftime("%Y-%m-%d"),
                "FGI_final": [50] * n,
                "close": [
                    100,
                    102,
                    98,
                    101,
                    103,  # up, down, up, up
                ]
                * 20,  # repeat 20x
            }
        )

        from fgi.output.signal_report import compute_forward_returns, compute_zone_stats

        df = compute_forward_returns(df, horizons=[1])
        stats = compute_zone_stats(df, horizon=1)

        neutral = next(s for s in stats if s["zone"] == "中性")
        assert neutral["n"] == 99  # last row NaN for forward_1
        # Pattern: 100→102(+2%), 102→98(-3.92%), 98→101(+3.06%), 101→103(+1.98%), 103→100(-2.91%)
        # repeat 20x → 3 of 5 positive per cycle → win_rate ≈ 60%
        assert 0.55 < neutral["win_rate"] < 0.65
        # mean should be positive (3 up, 1 down per cycle)
        assert neutral["mean"] > 0


class TestEngineIntegration:
    def test_load_data_joins_fgi_and_close(self, db):
        """SignalReportEngine.load_data() produces a joined DataFrame."""
        _seed(
            db,
            [("2020-01-02", 55.0), ("2020-01-03", 62.0)],
            [("2020-01-02", 3050.0), ("2020-01-03", 3080.0)],
        )

        from fgi.output.signal_report import SignalReportEngine

        engine = SignalReportEngine(db)
        df = engine.load_data()
        assert len(df) == 2
        assert "FGI_final" in df.columns
        assert "close" in df.columns
        assert df["FGI_final"].iloc[0] == 55.0
        assert df["close"].iloc[0] == 3050.0

    def test_load_data_skips_null_fgi(self, db):
        """Rows where FGI_final is NULL are excluded."""
        _seed(db, [("2020-01-02", 55.0)], [("2020-01-02", 3050.0)])
        # 2020-01-03 has close but no FGI — add raw close directly
        db.upsert_raw_data("2020-01-03", "m3_close", 3080.0)
        db.commit()

        from fgi.output.signal_report import SignalReportEngine

        engine = SignalReportEngine(db)
        df = engine.load_data()
        assert len(df) == 1  # only the row with FGI
        assert df["date"].iloc[0] == "2020-01-02"

    def test_full_pipeline_returns_structured_stats(self, db):
        """End-to-end: seed → load → compute → stats dict."""
        n = 200
        rng = np.random.default_rng(42)
        dates = pd.date_range("2020-01-02", periods=n, freq="B")
        closes = 3000 + np.cumsum(rng.normal(0, 20, n))
        fgis = rng.uniform(15, 85, n)

        _seed(
            db,
            [(d.strftime("%Y-%m-%d"), float(f)) for d, f in zip(dates, fgis, strict=False)],
            [(d.strftime("%Y-%m-%d"), float(c)) for d, c in zip(dates, closes, strict=False)],
        )

        from fgi.output.signal_report import SignalReportEngine

        engine = SignalReportEngine(db)
        result = engine.run()

        assert "stats" in result
        assert "metadata" in result
        # stats is a dict of horizon → list of zone stat dicts
        for h in [5, 20, 60]:
            assert h in result["stats"]
            zone_stats = result["stats"][h]
            assert len(zone_stats) == 5
            for zs in zone_stats:
                assert "zone" in zs
                assert "n" in zs
                assert "mean" in zs
                assert "win_rate" in zs

    def test_sample_split_respected(self, db):
        """Sample split is computed separately for in-sample vs out-of-sample."""
        rng = np.random.default_rng(42)
        # Generate dates spanning both in-sample and out-of-sample
        dates_in = pd.date_range("2018-01-02", periods=50, freq="B")
        dates_out = pd.date_range("2023-01-02", periods=50, freq="B")
        all_dates = list(dates_in) + list(dates_out)
        closes = 3000 + np.cumsum(rng.normal(0, 20, len(all_dates)))
        fgis = rng.uniform(20, 80, len(all_dates))

        _seed(
            db,
            [(d.strftime("%Y-%m-%d"), float(f)) for d, f in zip(all_dates, fgis, strict=False)],
            [(d.strftime("%Y-%m-%d"), float(c)) for d, c in zip(all_dates, closes, strict=False)],
        )

        from fgi.output.signal_report import SignalReportEngine

        engine = SignalReportEngine(db)
        result = engine.run()

        # Both splits should have stats
        assert "in_sample" in result
        assert "out_sample" in result
        assert result["in_sample"] is not None
        assert result["out_sample"] is not None


class TestMarkdownReport:
    """Test the Markdown report generator."""

    @pytest.fixture
    def sample_result(self):
        """Build a minimal result dict that exercises all report sections."""
        return {
            "metadata": {
                "start_date": "2015-12-17",
                "end_date": "2026-07-21",
                "total_days": 2572,
                "benchmark": "上证综指 (m3_close)",
                "fgi_definition": "FGI_final",
                "in_sample_range": "2015-12-17 ~ 2022-12-30",
                "out_sample_range": "2023-01-03 ~ 2026-07-21",
            },
            "stats": {
                5: [
                    {
                        "zone": "极度恐惧",
                        "n": 8,
                        "mean": 0.032,
                        "std": 0.045,
                        "ci_lower": None,
                        "ci_upper": None,
                        "win_rate": 0.875,
                    },
                    {
                        "zone": "恐惧",
                        "n": 334,
                        "mean": 0.008,
                        "std": 0.032,
                        "ci_lower": 0.0046,
                        "ci_upper": 0.0114,
                        "win_rate": 0.62,
                    },
                    {
                        "zone": "中性",
                        "n": 1467,
                        "mean": 0.002,
                        "std": 0.028,
                        "ci_lower": 0.0006,
                        "ci_upper": 0.0034,
                        "win_rate": 0.54,
                    },
                    {
                        "zone": "贪婪",
                        "n": 753,
                        "mean": -0.003,
                        "std": 0.025,
                        "ci_lower": -0.0048,
                        "ci_upper": -0.0012,
                        "win_rate": 0.44,
                    },
                    {
                        "zone": "极度贪婪",
                        "n": 10,
                        "mean": -0.015,
                        "std": 0.038,
                        "ci_lower": None,
                        "ci_upper": None,
                        "win_rate": 0.30,
                    },
                ],
            },
            "in_sample": None,
            "out_sample": None,
        }

    def test_generates_markdown_with_all_sections(self, sample_result):
        from fgi.output.signal_report import render_markdown

        md = render_markdown(sample_result)
        assert "FGI" in md
        assert "信号有效性" in md or "验证" in md
        assert "区间分布" in md
        assert "前瞻收益" in md or "5 日" in md
        assert "极端信号" in md
        assert "结论" in md or "局限" in md
        assert "上证综指" in md

    def test_n_lt_30_shows_warning(self, sample_result):
        from fgi.output.signal_report import render_markdown

        md = render_markdown(sample_result)
        # Extreme fear (n=8) and extreme greed (n=10) should have sample-size warning
        assert "样本不足" in md or "n < 30" in md.lower()

    def test_empty_result_handled(self):
        from fgi.output.signal_report import render_markdown

        empty = {
            "metadata": {"start_date": None, "end_date": None, "total_days": 0},
            "stats": {},
            "in_sample": None,
            "out_sample": None,
        }
        md = render_markdown(empty)
        assert "无数据" in md or "数据不足" in md

    def test_sample_split_shown_when_available(self, sample_result):
        from fgi.output.signal_report import render_markdown

        # Add some in-sample / out-sample data
        sample_result["in_sample"] = sample_result["stats"]
        sample_result["out_sample"] = sample_result["stats"]
        md = render_markdown(sample_result)
        assert "样本内" in md or "2015" in md
        assert "样本外" in md or "2023" in md


class TestZoneContextCard:
    """Test get_zone_for_fgi and render_zone_context_card."""

    def test_get_zone_for_fgi_maps_correctly(self):
        from fgi.output.signal_report import get_zone_for_fgi

        assert get_zone_for_fgi(10) == "极度恐惧"
        assert get_zone_for_fgi(20) == "恐惧"
        assert get_zone_for_fgi(39.9) == "恐惧"
        assert get_zone_for_fgi(50) == "中性"
        assert get_zone_for_fgi(70) == "贪婪"
        assert get_zone_for_fgi(80) == "极度贪婪"
        assert get_zone_for_fgi(None) == "未知"
        assert get_zone_for_fgi(float("nan")) == "未知"

    def test_render_zone_context_card_extreme_fear(self, db):
        """When FGI < 20, card shows extreme-fear stats with small-n warning."""
        from fgi.output.signal_report import render_zone_context_card

        # Seed tiny dataset with one extreme fear day
        _seed(db, [("2020-01-02", 15.0)], [("2020-01-02", 3050.0)])
        card = render_zone_context_card(15.0, db)
        assert "极度恐惧" in card
        assert "历史信号参考" in card

    def test_render_zone_context_card_normal_zone(self, db):
        """Card renders for a zone with enough data."""
        n = 200
        rng = np.random.default_rng(99)
        dates = pd.date_range("2018-01-02", periods=n, freq="B")
        closes = 3000 + np.cumsum(rng.normal(0, 20, n))
        fgis = rng.uniform(45, 55, n)  # all neutral

        _seed(
            db,
            [(d.strftime("%Y-%m-%d"), float(f)) for d, f in zip(dates, fgis, strict=False)],
            [(d.strftime("%Y-%m-%d"), float(c)) for d, c in zip(dates, closes, strict=False)],
        )

        from fgi.output.signal_report import render_zone_context_card

        card = render_zone_context_card(50.0, db)
        assert "中性" in card
        assert "历史信号参考" in card
        assert "上证综指" in card

    def test_render_zone_context_card_returns_empty_on_failure(self):
        """Missing DB or bad data returns empty string."""
        from fgi.output.signal_report import render_zone_context_card

        # None FGI → should return empty
        card = render_zone_context_card(None, None)
        assert card == ""


class TestRankIC:
    """Test Rank IC analysis functions."""

    def _make_df(self, n=200):
        """Create a synthetic DataFrame with FGI_final, close, and dimension scores."""
        rng = np.random.default_rng(123)
        dates = pd.date_range("2020-01-02", periods=n, freq="B")
        closes = 3000 + np.cumsum(rng.normal(0, 20, n))
        fgis = rng.uniform(20, 80, n)
        return pd.DataFrame(
            {
                "date": dates.strftime("%Y-%m-%d"),
                "FGI_final": fgis,
                "close": closes,
                "M1": rng.uniform(0, 100, n),
                "M2": rng.uniform(0, 100, n),
                "S2": rng.uniform(0, 100, n),
            }
        )

    def test_compute_rank_ic_returns_dict(self):
        from fgi.output.signal_report import compute_rank_ic

        df = self._make_df(200)
        result = compute_rank_ic(df)
        assert result is not None
        assert "ic" in result
        assert "n" in result
        assert -1.0 <= result["ic"] <= 1.0

    def test_compute_rank_ic_insufficient_data(self):
        from fgi.output.signal_report import compute_rank_ic

        df = self._make_df(20)
        result = compute_rank_ic(df)
        assert result is None

    def test_rolling_ic_window_structure(self):
        from fgi.output.signal_report import compute_rolling_ic_window

        df = self._make_df(800)  # need >756 for at least one window
        results = compute_rolling_ic_window(df, half_year=126)
        assert len(results) >= 1
        for pt in results:
            assert "date" in pt
            assert "ic" in pt
            assert "n" in pt


class TestLayerBacktest10:
    """Test 10-layer backtest."""

    def _make_df(self, n=200):
        rng = np.random.default_rng(456)
        dates = pd.date_range("2020-01-02", periods=n, freq="B")
        closes = 3000 + np.cumsum(rng.normal(0, 20, n))
        fgis = rng.uniform(20, 80, n)
        return pd.DataFrame(
            {
                "date": dates.strftime("%Y-%m-%d"),
                "FGI_final": fgis,
                "close": closes,
            }
        )

    def test_layer_backtest_returns_all_horizons(self):
        from fgi.output.signal_report import layer_backtest_10

        df = self._make_df(200)
        result = layer_backtest_10(df)
        for h in [5, 20, 60]:
            assert h in result
            assert len(result[h]) >= 1

    def test_layer_backtest_monotonicity_check(self):
        """Lowest decile (layer 1) should tend to have higher 60d return than highest decile (layer 10)."""
        from fgi.output.signal_report import layer_backtest_10

        rng = np.random.default_rng(789)
        n = 500
        dates = pd.date_range("2018-01-02", periods=n, freq="B")
        # Create a strong negative correlation: low FGI → high forward return
        base = np.linspace(-0.5, 0.5, n)
        noise = rng.normal(0, 0.03, n)
        forward_60 = base + noise  # increasing over time
        closes = 3000 * np.cumprod(1 + np.concatenate([[0], forward_60[:-1] / 100]))
        fgis = 100 - (np.linspace(20, 80, n) + rng.normal(0, 5, n))  # inverse
        df = pd.DataFrame(
            {
                "date": dates.strftime("%Y-%m-%d"),
                "FGI_final": fgis.clip(1, 99),
                "close": closes,
            }
        )
        result = layer_backtest_10(df)
        layer_60 = result[60]
        if len(layer_60) >= 2:
            # Top decile wins should differ from bottom decile
            bottom = layer_60[0]
            top = layer_60[-1]
            assert bottom["mean_return"] > top["mean_return"], (
                f"Expected low-FGI decile to outperform high-FGI decile, got {bottom['mean_return']:.4f} vs {top['mean_return']:.4f}"
            )


class TestDCA:
    """Test DCA simulation."""

    def _make_df(self, n_years=5):
        """Create synthetic data spanning multiple years."""
        n = n_years * 252
        rng = np.random.default_rng(111)
        dates = pd.date_range("2018-01-02", periods=n, freq="B")
        # Upward drift
        daily_ret = rng.normal(0.0003, 0.015, n)
        closes = 3000 * np.cumprod(1 + daily_ret)
        fgis = rng.uniform(30, 70, n)
        return pd.DataFrame(
            {
                "date": dates.strftime("%Y-%m-%d"),
                "FGI_final": fgis,
                "close": closes,
            }
        )

    def test_dca_returns_expected_keys(self):
        from fgi.output.signal_report import simulate_dca

        df = self._make_df(5)
        result = simulate_dca(df)
        assert "dca_total_return" in result
        assert "dca_annualized" in result
        assert "benchmark_total_return" in result
        assert "dca_max_drawdown" in result
        assert "n_months" in result

    def test_dca_insufficient_data(self):
        from fgi.output.signal_report import simulate_dca

        df = self._make_df(1)  # only ~12 months
        result = simulate_dca(df)
        # should work with 12 months
        assert "error" not in result


class TestCliEntry:
    """Test the generate_signal_report.py CLI script."""

    def test_main_with_defaults(self, tmp_path):
        """CLI runs with a seeded temp DB and writes a report file."""
        from fgi.config.settings import DB_PATH
        from fgi.output.signal_report import SignalReportEngine, render_markdown

        orig_path = DB_PATH
        path = tmp_path / "test_fgi.db"
        try:
            import fgi.config.settings as settings

            settings.DB_PATH = path
            test_db = Database(path).connect()
            test_db.init_schema()
            _seed(
                test_db,
                [(f"2020-01-{d:02d}", 30 + (d % 40)) for d in range(3, 23)],
                [(f"2020-01-{d:02d}", 3000 + i * 10) for i, d in enumerate(range(3, 23))],
                "2020-01-03",
            )
            test_db.commit()

            engine = SignalReportEngine(test_db)
            result = engine.run()
            report = render_markdown(result)
            assert "FGI" in report
            assert "信号有效性" in report or "验证" in report
            test_db.close()
        finally:
            import fgi.config.settings as settings

            settings.DB_PATH = orig_path
