"""Tests for PushPlus markdown rendering, especially health_score<60 warning."""
from fgi.output.pushplus import _fgi_header


class TestFgiHeader:
    def test_high_health_no_warning(self):
        out = _fgi_header(60.0, 85.0, "2026-07-23")
        assert "数据质量异常" not in out
        assert "**85** / 100" in out

    def test_low_health_appends_warning(self):
        out = _fgi_header(60.0, 55.0, "2026-07-23")
        assert "⚠️ 数据质量异常，仅供参考" in out
        assert "**55** / 100" in out

    def test_boundary_70_not_warned(self):
        """health == 70 不触发告警（threshold 已从 60 提升到 70）"""
        out = _fgi_header(60.0, 70.0, "2026-07-23")
        assert "数据质量异常" not in out

    def test_boundary_69_warned(self):
        """health == 69 触发告警"""
        out = _fgi_header(60.0, 69.0, "2026-07-23")
        assert "数据质量异常" in out

    def test_zero_health_warned(self):
        out = _fgi_header(60.0, 0.0, "2026-07-23")
        assert "数据质量异常" in out
