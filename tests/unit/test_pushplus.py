"""Tests for PushPlus markdown rendering, especially health_score<60 warning and FGI_LEVELS."""
from fgi.output.renderer import fgi_header, fgi_level, FGI_LEVELS


class TestFgiLevels:
    def test_fgi_levels_aligned_with_report_zones(self):
        """FGI_LEVELS thresholds should match signal_report zone boundaries: 20/40/60/80."""
        thresholds = [t for t, _ in FGI_LEVELS]
        assert thresholds == [20, 40, 60, 80], f"Expected [20,40,60,80], got {thresholds}"

    def test_extreme_fear(self):
        assert fgi_level(0) == "极度恐惧"
        assert fgi_level(10) == "极度恐惧"
        assert fgi_level(19.9) == "极度恐惧"

    def test_fear(self):
        assert fgi_level(20) == "恐惧"
        assert fgi_level(30) == "恐惧"

    def test_neutral(self):
        assert fgi_level(40) == "中性"
        assert fgi_level(55) == "中性"

    def test_greed(self):
        assert fgi_level(60) == "贪婪"
        assert fgi_level(79.9) == "贪婪"

    def test_extreme_greed(self):
        assert fgi_level(80) == "极度贪婪"
        assert fgi_level(95) == "极度贪婪"
        assert fgi_level(100) == "极度贪婪"


class TestFgiHeader:
    def test_high_health_no_warning(self):
        out = fgi_header(60.0, 85.0, "2026-07-23")
        assert "数据质量异常" not in out
        assert "**85** / 100" in out

    def test_low_health_appends_warning(self):
        out = fgi_header(60.0, 55.0, "2026-07-23")
        assert "⚠️ 数据质量异常，仅供参考" in out
        assert "**55** / 100" in out

    def test_boundary_60_not_warned(self):
        """health == 60 不触发告警（threshold=60，等号不触发）"""
        out = fgi_header(60.0, 60.0, "2026-07-23")
        assert "数据质量异常" not in out

    def test_boundary_59_warned(self):
        """health == 59 触发告警"""
        out = fgi_header(60.0, 59.0, "2026-07-23")
        assert "数据质量异常" in out

    def test_zero_health_warned(self):
        out = fgi_header(60.0, 0.0, "2026-07-23")
        assert "数据质量异常" in out
