"""web_export 的纯函数与导出契约。

导出脚本从 scripts/ 搬进包内后会被覆盖率门禁统计，这里覆盖不需要网络的部分：
分位标签、趋势、极端信号、维度均值、JSON 写入。
"""

import json
import tempfile
from pathlib import Path

import pytest

from fgi.output import web_export as wx


class TestPercentileLabel:
    @pytest.mark.parametrize(
        "pct,expected_label",
        [
            (1.0, "低于历史上 99% 的日子（极低）"),
            (10.0, "低于历史上 90% 的日子（极低）"),
            (20.0, "低于历史上 80% 的日子（偏低）"),
            (50.0, "位于历史中部（50%分位）"),
            (85.0, "高于历史上 85% 的日子（偏高）"),
            (99.9, "高于历史上 100% 的日子（极高）"),
        ],
    )
    def test_label_tiers(self, pct, expected_label):
        _, label, _ = wx._compute_percentile_label(pct)
        assert label == expected_label

    def test_extreme_note_only_at_tails(self):
        assert wx._compute_percentile_label(5.0)[2] != ""
        assert wx._compute_percentile_label(95.0)[2] != ""
        assert wx._compute_percentile_label(50.0)[2] == ""


class TestTrend:
    def test_flat_within_half_point(self):
        assert wx._get_trend(50.2, {"FGI_final": 50.0})[0] == "→"

    def test_up_and_down(self):
        assert wx._get_trend(60.0, {"FGI_final": 50.0})[0] == "↑"
        assert wx._get_trend(40.0, {"FGI_final": 50.0})[0] == "↓"

    def test_no_previous(self):
        arrow, delta = wx._get_trend(50.0, None)
        assert arrow == "→"
        assert delta is None

    def test_delta_is_signed(self):
        arrow, delta = wx._get_trend(40.0, {"FGI_final": 50.0})
        assert delta == pytest.approx(-10.0)


class TestExtremeSignals:
    def test_high_and_low_split(self):
        scores = {"M1": 95.0, "M2": 3.0, "M3": 50.0}
        ext = wx._get_extreme_signals(scores)
        assert [e[0] for e in ext["high"]] == ["M1"]
        assert [e[0] for e in ext["low"]] == ["M2"]

    def test_missing_values_are_ignored(self):
        ext = wx._get_extreme_signals({"M1": None, "M2": float("nan")})
        assert ext == {"high": [], "low": []}


class TestDimensionScores:
    def test_averages_present_indicators(self):
        from fgi.output.renderer import DIMENSION_INDICATORS

        dim, members = next(iter(DIMENSION_INDICATORS.items()))
        scores = dict.fromkeys(members, 60.0)
        assert wx._get_dimension_scores(scores)[dim] == 60.0

    def test_all_missing_gives_none(self):
        from fgi.output.renderer import DIMENSION_INDICATORS

        dim = next(iter(DIMENSION_INDICATORS))
        assert wx._get_dimension_scores({})[dim] is None


class TestRoundedPrev:
    def test_none_and_missing_key(self):
        assert wx._rounded_prev_fgi(None) is None
        assert wx._rounded_prev_fgi({}) is None
        assert wx._rounded_prev_fgi({"FGI_final": None}) is None

    def test_rounds_to_two_places(self):
        assert wx._rounded_prev_fgi({"FGI_final": 49.698851}) == 49.7
        assert wx._rounded_prev_fgi({"FGI_final": 49.123456}) == 49.12


class TestWriteJson:
    def test_writes_utf8_without_trailing_newline(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.json"
            wx.write_json({"区间": "恐惧", "n": 1}, path)
            raw = path.read_text(encoding="utf-8")
            assert "恐惧" in raw
            assert not raw.endswith("\n")
            assert json.loads(raw) == {"区间": "恐惧", "n": 1}
