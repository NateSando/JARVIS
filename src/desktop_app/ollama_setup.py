"""Silent Ollama bootstrap: replaces the old step-by-step setup wizard.

At start-up the desktop app runs this in the background:

1. find the Ollama CLI (if it is missing nothing else can work, so we only
   report how to install it);
2. start the Ollama server when it is not running and wait for it;
3. pull every required model that is missing, streaming progress.

No windows are shown: progress goes to the terminal and tray notifications.
All side effects are injected so the flow is testable without Ollama.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Callable, Optional

import requests

from jarvis.debug import debug_log

Report = Callable[[str], None]

INSTALL_HINT = "Ollama não encontrado. Instale em https://ollama.com/download e abra o JARVIS de novo."


def _normalise(name: str) -> str:
    return name[: -len(":latest")] if name.endswith(":latest") else name


def missing_models(required: list[str], installed: list[str]) -> list[str]:
    """Required models not present locally (``x`` and ``x:latest`` are the same model)."""
    have = {_normalise(m) for m in installed}
    return [m for m in required if _normalise(m) not in have]


def find_ollama_cli() -> Optional[str]:
    """Locate the ollama executable on PATH or in the usual install folders."""
    found = shutil.which("ollama")
    if found:
        return found
    candidates = [
        "/usr/local/bin/ollama",
        "/opt/homebrew/bin/ollama",
        os.path.expanduser("~/bin/ollama"),
    ]
    if sys.platform == "win32":
        candidates += [
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama.exe"),
            os.path.join(os.environ.get("PROGRAMFILES", ""), "Ollama", "ollama.exe"),
        ]
    return next((p for p in candidates if os.path.isfile(p) and os.access(p, os.X_OK)), None)


def _base_url() -> str:
    try:
        from jarvis.config import load_settings

        return load_settings().ollama_base_url
    except Exception:
        return "http://127.0.0.1:11434"


def is_ollama_server_up() -> bool:
    try:
        return requests.get(f"{_base_url()}/api/version", timeout=3).status_code == 200
    except requests.RequestException:
        return False


def _no_window() -> int:
    return subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0  # type: ignore[attr-defined]


def start_ollama_server(cli: str) -> None:
    if sys.platform == "darwin":
        try:
            subprocess.Popen(["open", "-a", "Ollama"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except OSError:
            pass
    subprocess.Popen(
        [cli, "serve"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=sys.platform != "win32",
        creationflags=_no_window(),
    )


def list_installed_models(cli: str) -> list[str]:
    try:
        result = subprocess.run(
            [cli, "list"], capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30, creationflags=_no_window(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    return [line.split()[0] for line in result.stdout.strip().splitlines()[1:] if line.strip()]


def pull_model(cli: str, model: str, report: Report) -> bool:
    """``ollama pull`` with progress lines forwarded to ``report``."""
    try:
        proc = subprocess.Popen(
            [cli, "pull", model], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", creationflags=_no_window(),
        )
    except OSError as e:
        report(f"❌ {model}: {e}")
        return False
    last = ""
    assert proc.stdout is not None
    for raw in proc.stdout:
        line = raw.strip().replace("\r", "")
        if line and line != last:
            last = line
            report(f"{model}: {line}")
    return proc.wait() == 0


def required_models_from_config() -> list[str]:
    from jarvis.config import load_settings

    cfg = load_settings()
    models: list[str] = []
    for name in (cfg.ollama_chat_model, cfg.ollama_embed_model, getattr(cfg, "intent_judge_model", "")):
        if name and name not in models:
            models.append(name)
    return models


@dataclass(frozen=True)
class BootstrapResult:
    ready: bool
    message: str


@dataclass
class OllamaBootstrap:
    find_cli: Callable[[], Optional[str]] = find_ollama_cli
    is_server_up: Callable[[], bool] = is_ollama_server_up
    start_server: Callable[[str], None] = start_ollama_server
    list_models: Callable[[str], list[str]] = list_installed_models
    pull_model: Callable[[str, str, Report], bool] = pull_model
    required_models: Callable[[], list[str]] = required_models_from_config
    sleep: Callable[[float], None] = time.sleep
    start_timeout_sec: float = 20.0

    def run(self, report: Report) -> BootstrapResult:
        cli = self.find_cli()
        if not cli:
            debug_log("ollama bootstrap: CLI not found", "desktop")
            return BootstrapResult(False, INSTALL_HINT)

        if not self.is_server_up():
            report("🚀 Iniciando o servidor Ollama…")
            try:
                self.start_server(cli)
            except OSError as e:
                return BootstrapResult(False, f"Não foi possível iniciar o Ollama: {e}")
            waited = 0.0
            while not self.is_server_up():
                if waited >= self.start_timeout_sec:
                    debug_log("ollama bootstrap: server did not come up", "desktop")
                    return BootstrapResult(False, "O servidor Ollama não respondeu. Rode 'ollama serve' e tente de novo.")
                self.sleep(0.5)
                waited += 0.5

        missing = missing_models(self.required_models(), self.list_models(cli))
        for model in missing:
            report(f"📥 Baixando o modelo {model} (uma única vez)…")
            if not self.pull_model(cli, model, report):
                return BootstrapResult(False, f"Falha ao baixar o modelo {model}. Verifique a internet e tente de novo.")
        debug_log(f"ollama bootstrap ready ({len(missing)} model(s) pulled)", "desktop")
        return BootstrapResult(True, "Ollama pronto.")
