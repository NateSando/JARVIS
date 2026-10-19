"""🧠 Local web server for the brain (cérebro) view.

Serves the Obsidian brain page and its data on 127.0.0.1 only. Runs inside
the desktop app process on a background thread (no subprocess, no external
network). See ``jarvis/brain/brain.spec.md``.
"""

from __future__ import annotations

import threading
from typing import Optional

from flask import Flask, Response, jsonify, redirect
from werkzeug.serving import BaseWSGIServer, make_server

from jarvis.config import load_settings
from jarvis.debug import debug_log
from jarvis.memory.graph import GraphMemoryStore

DEFAULT_PORT = 5050

app = Flask(__name__)

_graph_store: Optional[GraphMemoryStore] = None
_store_lock = threading.Lock()


def get_graph_store() -> GraphMemoryStore:
    global _graph_store
    with _store_lock:
        if _graph_store is None:
            _graph_store = GraphMemoryStore(load_settings().db_path)
        return _graph_store


@app.route("/")
def index() -> Response:
    return redirect("/cerebro")


@app.route("/cerebro")
def brain_page() -> str:
    from desktop_app.brain_page import render_brain_page

    return render_brain_page()


@app.route("/api/cerebro")
def brain_data() -> tuple[Response, int] | Response:
    """Sync the vault (idempotent) and return the brain map for the canvas."""
    try:
        settings = load_settings()
        if not getattr(settings, "obsidian_enabled", False):
            return jsonify({"enabled": False, "nodes": [], "links": [], "areas": []})
        from jarvis.brain import get_vault
        from jarvis.brain.brain_map import build_brain_map
        from jarvis.brain.sync import sync_brain
        from jarvis.system_prompt import build_system_prompt

        vault = get_vault(settings.obsidian_vault_path)
        sync_brain(vault, get_graph_store(), build_system_prompt())
        data = build_brain_map(vault)
        data.update(enabled=True, vault=str(vault.root))
        debug_log(f"brain map served: {len(data['nodes'])} nodes, {len(data['links'])} links", "brain")
        return jsonify(data)
    except Exception as e:
        debug_log(f"brain map failed: {e}", "brain")
        return jsonify({"enabled": True, "error": "Falha ao montar o cérebro.", "nodes": [], "links": [], "areas": []}), 500


class BrainServer:
    """The Flask app on a daemon thread, bound to localhost; ``port=0`` picks a free port."""

    def __init__(self, port: int = DEFAULT_PORT) -> None:
        self._requested_port = port
        self._server: Optional[BaseWSGIServer] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def port(self) -> int:
        return self._server.server_port if self._server else self._requested_port

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/cerebro"

    def start(self) -> None:
        if self._server is not None:
            return
        self._server = make_server("127.0.0.1", self._requested_port, app, threaded=True)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True, name="brain-server")
        self._thread.start()
        debug_log(f"brain server listening on {self.url}", "desktop")

    def stop(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        self._server = None
        debug_log("brain server stopped", "desktop")
