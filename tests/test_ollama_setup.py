"""Behaviour tests for the silent Ollama bootstrap that replaced the setup wizard."""

from __future__ import annotations

import pytest

from desktop_app.ollama_setup import OllamaBootstrap, missing_models


class FakeOllama:
    def __init__(self, cli: bool = True, running: bool = True, installed: list[str] | None = None,
                 starts: bool = True, failing_pulls: set[str] | None = None):
        self.cli = cli
        self.running = running
        self.installed = list(installed or [])
        self.starts = starts
        self.failing_pulls = failing_pulls or set()
        self.started = False
        self.pulled: list[str] = []

    def bootstrap(self, required: list[str]) -> OllamaBootstrap:
        return OllamaBootstrap(
            find_cli=lambda: "/usr/bin/ollama" if self.cli else None,
            is_server_up=lambda: self.running,
            start_server=self._start,
            list_models=lambda cli: list(self.installed),
            pull_model=self._pull,
            required_models=lambda: list(required),
            sleep=lambda s: None,
            start_timeout_sec=1.0,
        )

    def _start(self, cli: str) -> None:
        self.started = True
        if self.starts:
            self.running = True

    def _pull(self, cli: str, model: str, report) -> bool:
        if model in self.failing_pulls:
            return False
        report(f"{model}: 100%")
        self.pulled.append(model)
        self.installed.append(model)
        return True


@pytest.mark.unit
class TestMissingModels:
    def test_latest_tag_is_equivalent_to_no_tag(self):
        assert missing_models(["nomic-embed-text"], ["nomic-embed-text:latest"]) == []
        assert missing_models(["llama3:latest"], ["llama3"]) == []

    def test_different_tags_are_distinct(self):
        assert missing_models(["gemma4:e2b"], ["gemma4:e4b"]) == ["gemma4:e2b"]


@pytest.mark.unit
class TestBootstrap:
    def test_ready_when_everything_is_in_place(self):
        fake = FakeOllama(installed=["a", "b"])
        result = fake.bootstrap(["a", "b"]).run(report=lambda m: None)
        assert result.ready
        assert not fake.started and fake.pulled == []

    def test_missing_cli_is_reported_without_side_effects(self):
        fake = FakeOllama(cli=False, running=False)
        messages: list[str] = []
        result = fake.bootstrap(["a"]).run(report=messages.append)
        assert not result.ready
        assert "ollama" in result.message.lower()
        assert not fake.started and fake.pulled == []

    def test_stopped_server_is_started(self):
        fake = FakeOllama(running=False, installed=["a"])
        result = fake.bootstrap(["a"]).run(report=lambda m: None)
        assert fake.started
        assert result.ready

    def test_server_that_never_comes_up_is_reported(self):
        fake = FakeOllama(running=False, starts=False)
        result = fake.bootstrap(["a"]).run(report=lambda m: None)
        assert not result.ready
        assert fake.pulled == []

    def test_missing_models_are_pulled_in_order_with_progress(self):
        fake = FakeOllama(installed=["a"])
        messages: list[str] = []
        result = fake.bootstrap(["a", "b", "c"]).run(report=messages.append)
        assert result.ready
        assert fake.pulled == ["b", "c"]
        assert any("b" in m for m in messages)

    def test_failed_pull_is_reported(self):
        fake = FakeOllama(installed=[], failing_pulls={"b"})
        result = fake.bootstrap(["a", "b"]).run(report=lambda m: None)
        assert not result.ready
        assert "b" in result.message
