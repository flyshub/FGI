"""Workflow 文件里那些没法被 ruff/mypy 守住的约定。

两处最容易在后续维护中悄悄失效：
1. 兜底 cron 的时间字符串在 schedule 与守卫脚本里各写一份——改一处就会让守卫失效，
   表现为「每天跑两次、推两次」，且不报错。
2. workflow 里 `python -m fgi.output.xxx` 的模块名——模块被改名/搬走后会静默失败，
   直到某天定时任务红掉才发现。
"""

import importlib
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))


def _text(name: str) -> str:
    return (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")


class TestFallbackCronGuard:
    def test_guard_string_is_one_of_the_schedule_crons(self):
        text = _text("daily_update.yml")
        crons = re.findall(r"- cron: '([^']+)'", text)
        guard = re.search(r'\$EVENT_SCHEDULE" = "([^"]+)"', text)

        assert len(crons) == 2, f"期望主跑 + 兜底两条 cron，实际 {crons}"
        assert guard is not None, "找不到兜底守卫里的 EVENT_SCHEDULE 比较"
        assert guard.group(1) in crons, (
            f"守卫比较的 {guard.group(1)!r} 不在 schedule 里（{crons}）——改 cron 时忘了同步守卫"
        )
        assert guard.group(1) != crons[0], "守卫不应作用于主跑那一趟"


class TestWorkflowModulesExist:
    @pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda p: p.name)
    def test_python_dash_m_modules_import(self, workflow):
        text = workflow.read_text(encoding="utf-8")
        modules = set(re.findall(r"python -m (fgi[\w.]*)", text))
        for module in sorted(modules):
            importlib.import_module(module)
