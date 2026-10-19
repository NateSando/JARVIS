"""Tests for the brain (cérebro) server: the only web page the desktop app serves."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

try:
    import flask  # noqa: F401

    _HAS_FLASK = True
except ImportError:
    _HAS_FLASK = False

from jarvis.memory.graph import GraphMemoryStore


@pytest.mark.unit
@pytest.mark.skipif(not _HAS_FLASK, reason="Flask not available")
class TestBrainEndpoints:
    @pytest.fixture(autouse=True)
    def setup_app(self, tmp_path, monkeypatch):
        from desktop_app import brain_server

        self.store = GraphMemoryStore(str(tmp_path / "mem.db"))
        self.vault_path = tmp_path / "vault"
        self.enabled = True
        monkeypatch.setattr(brain_server, "_graph_store", self.store)
        monkeypatch.setattr(
            brain_server,
            "load_settings",
            lambda: SimpleNamespace(
                obsidian_enabled=self.enabled,
                obsidian_vault_path=str(self.vault_path),
                db_path=str(tmp_path / "mem.db"),
            ),
        )
        brain_server.app.config["TESTING"] = True
        self.client = brain_server.app.test_client()
        yield
        self.store.close()

    def test_brain_data_includes_skills_memory_and_points(self):
        self.store.create_node("Programação", "Linguagens", data="Usa Python", parent_id="user")
        resp = self.client.get("/api/cerebro")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["enabled"] is True
        labels = {n["label"]: n for n in data["nodes"]}
        assert labels["Programação"]["points"] == ["Usa Python"]
        assert any(n["kind"] == "area" for n in data["nodes"])
        assert any(n["kind"] == "concept" for n in data["nodes"])
        assert data["links"]
        # The vault is real Markdown that Obsidian can open
        assert any(self.vault_path.rglob("*.md"))

    def test_disabled_brain_reports_it(self):
        self.enabled = False
        data = self.client.get("/api/cerebro").get_json()
        assert data["enabled"] is False
        assert data["nodes"] == []

    def test_brain_page_is_served_offline(self):
        resp = self.client.get("/cerebro")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "<canvas" in html
        assert "/api/cerebro" in html
        # No external scripts: the brain must work without internet access
        assert "<script src=" not in html

    def test_root_opens_the_brain(self):
        resp = self.client.get("/")
        assert resp.status_code in (301, 302)
        assert resp.headers["Location"].endswith("/cerebro")

    def test_server_starts_and_stops_on_a_free_port(self):
        from urllib.request import urlopen
        from desktop_app import brain_server

        server = brain_server.BrainServer(port=0)
        server.start()
        try:
            with urlopen(f"http://127.0.0.1:{server.port}/cerebro", timeout=5) as resp:
                assert resp.status == 200
        finally:
            server.stop()
