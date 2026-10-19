"""Behaviour tests for the read-only, permission-gated systemInfo tool."""

from __future__ import annotations

import platform
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from jarvis.tools.base import ToolContext
from jarvis.tools.builtin.code_access.system_info import SystemInfoTool


def _ctx(enabled: bool) -> ToolContext:
    ctx = Mock(spec=ToolContext)
    ctx.cfg = SimpleNamespace(system_access_enabled=enabled)
    ctx.user_print = Mock()
    return ctx


@pytest.fixture(autouse=True)
def real_psutil(monkeypatch):
    """Some suites stub ``psutil`` in sys.modules globally; these tests need the real one."""
    import importlib
    import sys
    from unittest.mock import Mock as _Mock

    if isinstance(sys.modules.get("psutil"), _Mock):
        monkeypatch.delitem(sys.modules, "psutil")
        try:
            module = importlib.import_module("psutil")
        except ImportError:
            pytest.skip("psutil not installed")
        monkeypatch.setitem(sys.modules, "psutil", module)


@pytest.mark.unit
class TestSystemInfo:
    def test_disabled_by_default_reveals_nothing(self):
        result = SystemInfoTool().run({"section": "overview"}, _ctx(False))
        assert result.success is False
        assert "system_access_enabled" in result.reply_text
        assert platform.system() not in result.reply_text

    def test_overview_reports_os_cpu_and_memory(self):
        result = SystemInfoTool().run({"section": "overview"}, _ctx(True))
        assert result.success is True
        assert platform.system() in result.reply_text
        assert "CPU" in result.reply_text
        assert "RAM" in result.reply_text

    def test_processes_lists_the_heaviest_ones(self):
        result = SystemInfoTool().run({"section": "processes"}, _ctx(True))
        assert result.success is True
        assert "PID" in result.reply_text

    def test_disks_reports_usage(self):
        result = SystemInfoTool().run({"section": "disks"}, _ctx(True))
        assert result.success is True
        assert "%" in result.reply_text

    def test_defaults_to_overview(self):
        assert "CPU" in SystemInfoTool().run({}, _ctx(True)).reply_text

    def test_unknown_section_is_rejected(self):
        assert SystemInfoTool().run({"section": "shell"}, _ctx(True)).success is False

    def test_process_command_lines_are_not_exposed(self):
        # Command lines often carry tokens/passwords; only names are reported.
        text = SystemInfoTool().run({"section": "processes"}, _ctx(True)).reply_text
        assert "--password" not in text and "token=" not in text.lower()
