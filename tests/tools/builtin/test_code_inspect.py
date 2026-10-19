"""Behaviour tests for the read-only, permission-gated codeInspect tool."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from jarvis.tools.base import ToolContext
from jarvis.tools.builtin.code_access.code_inspect import CodeInspectTool


def _ctx(roots: list[str]) -> ToolContext:
    ctx = Mock(spec=ToolContext)
    ctx.cfg = SimpleNamespace(code_access_roots=roots)
    ctx.user_print = Mock()
    return ctx


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "meu-projeto"
    (root / "src" / "app").mkdir(parents=True)
    (root / "node_modules" / "lib").mkdir(parents=True)
    (root / ".git").mkdir()
    (root / "package.json").write_text('{"name": "meu-projeto", "dependencies": {"express": "^4"}}')
    (root / "README.md").write_text("# Meu projeto\n")
    (root / "src" / "app" / "service.py").write_text(
        "import os\n\n"
        "class PaymentService:\n"
        "    def charge(self, amount: int) -> bool:\n"
        "        token = 'abc'\n"
        "        return amount > 0\n\n"
        "def helper() -> None:\n"
        "    pass\n"
    )
    (root / "src" / "app" / "routes.ts").write_text(
        "export function listUsers() { return [] }\nexport class UserController {}\n"
    )
    (root / "src" / "config.py").write_text("API_KEY = 'sk-" + "a" * 40 + "'\npassword = hunter2\n")
    (root / ".env").write_text("DATABASE_URL=postgres://user:segredo@db/prod\n")
    (root / "id_rsa").write_text("-----BEGIN OPENSSH PRIVATE KEY-----\n")
    (root / "node_modules" / "lib" / "index.js").write_text("module.exports = 1\n")
    (root / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00binary")
    return root


def _run(tool: CodeInspectTool, roots: list[str], **args):
    return tool.run(args, _ctx(roots))


@pytest.mark.unit
class TestPermission:
    def test_without_granted_folders_nothing_is_read(self, project: Path):
        result = _run(CodeInspectTool(), [], operation="overview", path=str(project))
        assert result.success is False
        assert "code_access_roots" in result.reply_text
        assert "PaymentService" not in result.reply_text

    def test_paths_outside_granted_folders_are_refused(self, project: Path, tmp_path: Path):
        outside = tmp_path / "outro"
        outside.mkdir()
        (outside / "x.py").write_text("SECRET_THING = 1\n")
        result = _run(CodeInspectTool(), [str(project)], operation="read", path=str(outside / "x.py"))
        assert result.success is False
        assert "SECRET_THING" not in (result.reply_text or "")

    def test_traversal_out_of_a_root_is_refused(self, project: Path):
        result = _run(CodeInspectTool(), [str(project)], operation="tree", path=str(project / ".." ))
        assert result.success is False

    @pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
    def test_symlink_escape_is_refused(self, project: Path, tmp_path: Path):
        secret = tmp_path / "fora.txt"
        secret.write_text("conteudo externo\n")
        (project / "atalho.txt").symlink_to(secret)
        result = _run(CodeInspectTool(), [str(project)], operation="read", path=str(project / "atalho.txt"))
        assert result.success is False
        assert "conteudo externo" not in (result.reply_text or "")

    def test_roots_operation_lists_what_was_granted(self, project: Path):
        result = _run(CodeInspectTool(), [str(project)], operation="roots")
        assert result.success is True
        assert str(project) in result.reply_text


@pytest.mark.unit
class TestSecrets:
    def test_secret_files_are_never_read(self, project: Path):
        for name in (".env", "id_rsa"):
            result = _run(CodeInspectTool(), [str(project)], operation="read", path=str(project / name))
            assert result.success is False
        found = _run(CodeInspectTool(), [str(project)], operation="search", path=str(project), query="postgres").reply_text
        assert ".env" not in found and "db/prod" not in found

    def test_credentials_inside_code_are_redacted(self, project: Path):
        result = _run(CodeInspectTool(), [str(project)], operation="read", path=str(project / "src" / "config.py"))
        assert result.success is True
        assert "sk-aaaa" not in result.reply_text
        assert "hunter2" not in result.reply_text
        assert "[REDACTED" in result.reply_text


@pytest.mark.unit
class TestStructure:
    def test_overview_summarises_languages_manifests_and_layout(self, project: Path):
        result = _run(CodeInspectTool(), [str(project)], operation="overview", path=str(project))
        assert result.success is True
        text = result.reply_text
        assert "Python" in text and "TypeScript" in text
        assert "package.json" in text
        assert "src/" in text
        assert "node_modules" not in text

    def test_tree_skips_vendor_and_vcs_folders(self, project: Path):
        text = _run(CodeInspectTool(), [str(project)], operation="tree", path=str(project), depth=4).reply_text
        assert "service.py" in text and "routes.ts" in text
        assert "node_modules" not in text and ".git" not in text

    def test_outline_lists_symbols_with_lines(self, project: Path):
        py = _run(CodeInspectTool(), [str(project)], operation="outline", path=str(project / "src" / "app" / "service.py")).reply_text
        assert "class PaymentService" in py and "L3" in py
        assert "def charge" in py and "def helper" in py
        ts = _run(CodeInspectTool(), [str(project)], operation="outline", path=str(project / "src" / "app" / "routes.ts")).reply_text
        assert "listUsers" in ts and "UserController" in ts

    def test_search_finds_matches_with_file_and_line(self, project: Path):
        text = _run(CodeInspectTool(), [str(project)], operation="search", path=str(project), query="PaymentService").reply_text
        assert "src/app/service.py:3" in text

    def test_search_skips_binary_and_vendor_files(self, project: Path):
        text = _run(CodeInspectTool(), [str(project)], operation="search", path=str(project), query="module.exports").reply_text
        assert "node_modules" not in text

    def test_read_returns_numbered_line_ranges(self, project: Path):
        text = _run(
            CodeInspectTool(), [str(project)], operation="read",
            path=str(project / "src" / "app" / "service.py"), startLine=3, endLine=4,
        ).reply_text
        assert "3 | class PaymentService:" in text
        assert "import os" not in text

    def test_relative_paths_resolve_inside_the_single_root(self, project: Path):
        text = _run(CodeInspectTool(), [str(project)], operation="read", path="src/app/routes.ts").reply_text
        assert "listUsers" in text

    def test_unknown_operation_is_rejected(self, project: Path):
        result = _run(CodeInspectTool(), [str(project)], operation="delete", path=str(project))
        assert result.success is False
        assert project.exists()


@pytest.mark.unit
class TestWiring:
    def test_access_is_off_until_the_user_grants_it(self):
        from jarvis.config import get_default_config

        defaults = get_default_config()
        assert defaults["code_access_roots"] == []
        assert defaults["system_access_enabled"] is False

    def test_granted_folders_load_from_config(self, tmp_path: Path, monkeypatch):
        import json
        from jarvis import config as cfg_mod

        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({"code_access_roots": [str(tmp_path)], "system_access_enabled": True}))
        monkeypatch.setenv("JARVIS_CONFIG_PATH", str(cfg_file))
        settings = cfg_mod.load_settings()
        assert settings.code_access_roots == [str(tmp_path)]
        assert settings.system_access_enabled is True

    def test_tools_are_registered_for_the_reply_engine(self):
        from jarvis.tools.registry import BUILTIN_TOOLS

        assert "codeInspect" in BUILTIN_TOOLS and "systemInfo" in BUILTIN_TOOLS
