import pytest

from fgi.output import daily_run
from fgi.output.daily_run import is_trading_day, setup_data_manager


class TestTradingDay:
    def test_calendar_trading_day(self):
        days = ["2024-01-02", "2024-01-03"]
        assert is_trading_day("2024-01-02", trading_days=days) is True

    def test_calendar_holiday_weekday(self):
        # 2024-01-01 是周一但元旦休市：真实日历里不存在则跳过
        days = ["2024-01-02", "2024-01-03"]
        assert is_trading_day("2024-01-01", trading_days=days) is False

    def test_calendar_weekend(self):
        days = ["2024-01-02"]
        assert is_trading_day("2024-01-06", trading_days=days) is False

    def test_calendar_unavailable_raises(self, monkeypatch):
        # 日历不可用时不再猜「周一至周五」：节假日会被当成交易日，
        # 昨日分数会以 normal 状态推送为今日（P0-2）。
        monkeypatch.setattr(daily_run.TradingCalendar, "load", lambda self: None)
        with pytest.raises(daily_run.TradingCalendarUnavailable):
            is_trading_day("2024-01-01")

    def test_main_skips_non_trading_day(self, monkeypatch, capsys):
        monkeypatch.setattr(daily_run, "is_trading_day", lambda d: False)
        monkeypatch.setattr("sys.argv", ["daily_run", "--date", "2024-01-06"])
        daily_run.main()
        assert "not a trading day" in capsys.readouterr().out


class TestSetupDataManager:
    def test_setup(self):
        manager = setup_data_manager()
        assert manager is not None
        assert len(manager._sources) > 0

    def test_no_s1_chain(self):
        manager = setup_data_manager()
        assert "s1_sentiment_zz" not in manager._chains
        assert "s1_index" not in manager._chains

    def test_no_mock_source(self):
        manager = setup_data_manager()
        assert "mock" not in manager._sources


class TestAnomalyGating:
    """#34: 当 is_anomaly=True 时 daily_run 不调用 send_fgi_report（spec line 262）"""

    def _patch_run_path(
        self,
        monkeypatch,
        anomaly_detected: bool,
        calls: dict,
        result_overrides: dict | None = None,
        push_result: bool = True,
    ):
        """Patch calculator.run / Alert.check_and_alert / send_fgi_report / record_indicator_status."""
        monkeypatch.setattr(daily_run, "is_trading_day", lambda d: True)
        monkeypatch.setattr(daily_run, "setup_data_manager", lambda: object())

        class _FakeDb:
            path = ":memory:"

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def init_schema(self):
                pass

        base_result = {
            "fgi_final": 70.0,
            "fgi_raw": 70.0,
            "health_score": 80.0,
            "dimension_scores": {},
            "indicator_results": {},
            "error_count": 0,
            "degraded_count": 0,
            "missing_count": 0,
            "ok_count": 12,
        }
        base_result.update(result_overrides or {})

        monkeypatch.setattr(daily_run, "Database", lambda *a, **k: _FakeDb())
        monkeypatch.setattr(
            daily_run,
            "FGICalculator",
            lambda *a, **k: type(
                "C",
                (),
                {"run": staticmethod(lambda d: dict(base_result))},
            ),
        )
        monkeypatch.setattr(daily_run, "record_indicator_status", lambda *a, **k: None)
        monkeypatch.setattr(
            "fgi.output.alert.Alert.check_and_alert", lambda self, d, r: anomaly_detected
        )
        monkeypatch.setattr(
            daily_run,
            "send_fgi_report",
            lambda db, *a, **k: calls.setdefault("pushed", push_result) or push_result,
        )
        monkeypatch.setattr("sys.argv", ["daily_run", "--date", "2024-01-02"])

    def test_no_anomaly_sends_push(self, monkeypatch, capsys):
        calls = {}
        self._patch_run_path(monkeypatch, anomaly_detected=False, calls=calls)
        daily_run.main()
        assert calls.get("pushed") is True

    def test_anomaly_blocks_push(self, monkeypatch, capsys):
        calls = {}
        self._patch_run_path(monkeypatch, anomaly_detected=True, calls=calls)
        daily_run.main()
        assert "pushed" not in calls
        assert "manual review required" in capsys.readouterr().out

    def test_force_push_bypasses_anomaly_check(self, monkeypatch, capsys):
        """--force-push：人工复核通过后跳过异常检测直接推送。"""
        calls = {}
        self._patch_run_path(monkeypatch, anomaly_detected=True, calls=calls)
        monkeypatch.setattr(
            "sys.argv",
            ["daily_run", "--date", "2024-01-02", "--force-push"],
        )
        daily_run.main()
        assert calls.get("pushed") is True


class TestFailureVisibility:
    """P0-3 / P0-5：失败必须可见，不能静默输出一个看起来正常的数字。"""

    def test_error_threshold_suppresses_push(self, monkeypatch, capsys):
        calls = {}
        TestAnomalyGating()._patch_run_path(
            monkeypatch,
            anomaly_detected=False,
            calls=calls,
            result_overrides={"error_count": daily_run.ERROR_SUPPRESS_THRESHOLD},
        )
        with pytest.raises(SystemExit) as exc:
            daily_run.main()
        assert exc.value.code == 1
        assert "pushed" not in calls
        assert "Push suppressed" in capsys.readouterr().err

    def test_below_threshold_still_pushes(self, monkeypatch):
        calls = {}
        TestAnomalyGating()._patch_run_path(
            monkeypatch,
            anomaly_detected=False,
            calls=calls,
            result_overrides={"error_count": daily_run.ERROR_SUPPRESS_THRESHOLD - 1},
        )
        daily_run.main()
        assert calls.get("pushed") is True

    def test_first_pass_also_fails_loud(self, monkeypatch):
        """--first-pass 是干跑，但指标大面积崩溃同样要非零退出——失败判定优先于阶段。"""
        calls = {}
        TestAnomalyGating()._patch_run_path(
            monkeypatch,
            anomaly_detected=False,
            calls=calls,
            result_overrides={"error_count": 6},
        )
        monkeypatch.setattr("sys.argv", ["daily_run", "--date", "2024-01-02", "--first-pass"])
        with pytest.raises(SystemExit) as exc:
            daily_run.main()
        assert exc.value.code == 1
        assert "pushed" not in calls

    def test_no_fgi_suppresses_push(self, monkeypatch, capsys):
        """全源故障时 fgi_final 为 None：没有任何东西可发布，必须非零退出。"""
        calls = {}
        TestAnomalyGating()._patch_run_path(
            monkeypatch,
            anomaly_detected=False,
            calls=calls,
            result_overrides={"fgi_final": None, "fgi_raw": None},
        )
        with pytest.raises(SystemExit) as exc:
            daily_run.main()
        assert exc.value.code == 1
        assert "pushed" not in calls
        assert "No FGI produced" in capsys.readouterr().err

    def test_push_failure_exits_nonzero(self, monkeypatch, capsys):
        calls = {}
        TestAnomalyGating()._patch_run_path(
            monkeypatch, anomaly_detected=False, calls=calls, push_result=False
        )
        with pytest.raises(SystemExit) as exc:
            daily_run.main()
        assert exc.value.code == 1
        assert "not delivered" in capsys.readouterr().err

    def test_stale_indicators_are_reported(self, monkeypatch, capsys):
        calls = {}
        TestAnomalyGating()._patch_run_path(
            monkeypatch,
            anomaly_detected=False,
            calls=calls,
            result_overrides={
                "indicator_results": {
                    "M4": {"score": 50.0, "status": "degraded", "source_date": "2024-01-01"},
                    "M1": {"score": 50.0, "status": "normal", "source_date": "2024-01-02"},
                }
            },
        )
        daily_run.main()
        out = capsys.readouterr().out
        assert "Stale data: 1 indicators" in out
        assert "M4" in out
        assert "M1" not in out.split("Stale data:")[1]


class TestSharedFactory:
    """P2-7：daily_run 与 backfill 必须共用同一份数据源装配。"""

    def test_both_entrypoints_use_collector_factory(self):
        import fgi.collector.factory as factory
        import fgi.output.backfill as backfill

        assert daily_run.setup_data_manager is factory.setup_data_manager
        assert backfill.setup_data_manager is factory.setup_data_manager

    def test_backfill_opts_out_of_extra_fallbacks(self):
        """历史回填只信 akshare：mootdx/tencent 窗口只有几百天。"""
        import inspect

        import fgi.collector.factory as factory

        sig = inspect.signature(factory.setup_data_manager)
        assert sig.parameters["extra_fallbacks"].default is True
        source = inspect.getsource(
            __import__("fgi.output.backfill", fromlist=["backfill"]).backfill
        )
        assert "extra_fallbacks=False" in source
