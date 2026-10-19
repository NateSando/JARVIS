"""A broken or missing ``mcp`` package must never stop Jarvis from starting.

Regression: pydantic 2.14 removed ``eval_type_backport``, which mcp 1.13.1
imports, so ``import mcp`` raised ImportError and took the whole daemon down
through ``tools.registry``.
"""

from __future__ import annotations

import builtins
import importlib
import sys

import pytest


@pytest.fixture
def broken_mcp(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "mcp" or name.startswith("mcp."):
            raise ImportError("cannot import name 'eval_type_backport'")
        return real_import(name, *args, **kwargs)

    for mod in [m for m in sys.modules if m == "mcp" or m.startswith("mcp.") or m.startswith("jarvis.tools")]:
        monkeypatch.delitem(sys.modules, mod)
    monkeypatch.setattr(builtins, "__import__", fake_import)
    yield
    monkeypatch.setattr(builtins, "__import__", real_import)
    for mod in [m for m in sys.modules if m.startswith("jarvis.tools")]:
        sys.modules.pop(mod, None)


@pytest.mark.unit
def test_tools_load_without_mcp(broken_mcp):
    registry = importlib.import_module("jarvis.tools.registry")
    assert "codeInspect" in registry.BUILTIN_TOOLS


@pytest.mark.unit
def test_configured_servers_report_an_error_instead_of_crashing(broken_mcp):
    registry = importlib.import_module("jarvis.tools.registry")
    tools, errors = registry.discover_mcp_tools({"github": {"command": "npx", "args": []}})
    assert tools == {}
    assert "mcp" in errors["github"].lower()


@pytest.mark.unit
def test_requirements_keep_pydantic_compatible_with_pinned_mcp():
    from pathlib import Path

    reqs = (Path(__file__).resolve().parents[1] / "requirements.txt").read_text()
    pins = {line.split("==")[0].split(">=")[0].strip(): line for line in reqs.splitlines() if line and not line.startswith("#")}
    assert "pydantic" in pins and "<2.14" in pins["pydantic"], "mcp 1.13.x needs pydantic < 2.14"
