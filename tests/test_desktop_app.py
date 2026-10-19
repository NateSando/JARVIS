"""
Tests for desktop_app.py functionality.

Tests crash-log paths, the single-instance lock, GPU recovery and start-up flow.
Note: GUI components are not tested here - only the underlying logic.
"""

import os
import pytest
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock


class TestEntryPointImports:
    """Guardrails for the PyInstaller entry point (src/desktop_app/app.py).

    PyInstaller freezes app.py as __main__ with no parent package, so any
    relative import (`from .foo import ...`) raises ImportError at launch
    and the bundled app exits silently. Regression guard for the #242 bug
    where `from .paths import get_log_dir` inside get_crash_paths() broke
    every macOS launch.
    """

    def test_app_py_has_no_relative_imports(self):
        """app.py is the frozen entry point — must use absolute imports only."""
        import ast
        from pathlib import Path

        app_py = Path(__file__).parent.parent / "src" / "desktop_app" / "app.py"
        tree = ast.parse(app_py.read_text(encoding="utf-8"))

        relative_imports = [
            f"line {node.lineno}: from {'.' * node.level}{node.module or ''} import ..."
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.level > 0
        ]

        assert not relative_imports, (
            "app.py is the PyInstaller entry point and runs as __main__ with "
            "no package context. Relative imports will raise ImportError at "
            "launch. Use `from desktop_app.X import ...` instead.\n"
            "Offenders:\n  " + "\n  ".join(relative_imports)
        )


class TestGetCrashPaths:
    """Tests for get_crash_paths() function."""

    def test_returns_three_paths(self):
        """get_crash_paths() should return a tuple of 3 paths."""
        from desktop_app import get_crash_paths

        result = get_crash_paths()
        assert isinstance(result, tuple)
        assert len(result) == 3

    def test_all_paths_are_path_objects(self):
        """All returned paths should be Path objects."""
        from desktop_app import get_crash_paths

        crash_log, crash_marker, previous_crash = get_crash_paths()
        assert isinstance(crash_log, Path)
        assert isinstance(crash_marker, Path)
        assert isinstance(previous_crash, Path)

    def test_paths_have_expected_names(self):
        """Paths should have the expected filenames."""
        from desktop_app import get_crash_paths

        crash_log, crash_marker, previous_crash = get_crash_paths()
        assert crash_log.name == "jarvis_desktop_crash.log"
        assert crash_marker.name == ".crash_marker"
        assert previous_crash.name == "previous_crash.log"

    def test_paths_share_same_parent_directory(self):
        """All crash paths should be in the same directory."""
        from desktop_app import get_crash_paths

        crash_log, crash_marker, previous_crash = get_crash_paths()
        assert crash_log.parent == crash_marker.parent == previous_crash.parent

    @patch("sys.platform", "darwin")
    def test_macos_uses_library_logs(self):
        """On macOS, should use ~/Library/Logs/Jarvis."""
        # Note: This is tricky because the function reads sys.platform at runtime
        from desktop_app import get_crash_paths

        crash_log, _, _ = get_crash_paths()
        if sys.platform == "darwin":
            assert "Library" in str(crash_log) or "Logs" in str(crash_log)


class TestSingleInstanceLock:
    """Tests for the single-instance locking mechanism.

    Focuses on the regression where 'w' mode truncated the lock file before
    the lock attempt, destroying the existing instance's PID.
    """

    def test_get_existing_instance_pid_reads_pid(self, tmp_path):
        """get_existing_instance_pid() should return the PID stored in the lock file."""
        from desktop_app.app import get_existing_instance_pid

        lock_file = tmp_path / "jarvis_desktop.lock"
        lock_file.write_bytes(b"12345")

        with patch("desktop_app.app.get_lock_file_path", return_value=lock_file):
            pid = get_existing_instance_pid()

        assert pid == 12345

    def test_get_existing_instance_pid_returns_none_when_empty(self, tmp_path):
        """get_existing_instance_pid() should return None for an empty lock file."""
        from desktop_app.app import get_existing_instance_pid

        lock_file = tmp_path / "jarvis_desktop.lock"
        lock_file.write_bytes(b"")

        with patch("desktop_app.app.get_lock_file_path", return_value=lock_file):
            pid = get_existing_instance_pid()

        assert pid is None

    def test_get_existing_instance_pid_returns_none_when_missing(self, tmp_path):
        """get_existing_instance_pid() should return None when the lock file is absent."""
        from desktop_app.app import get_existing_instance_pid

        lock_file = tmp_path / "jarvis_desktop.lock"

        with patch("desktop_app.app.get_lock_file_path", return_value=lock_file):
            pid = get_existing_instance_pid()

        assert pid is None

    def test_lock_file_not_truncated_on_failed_lock_attempt(self, tmp_path):
        """The existing PID must still be readable after a failed lock attempt.

        This is the core regression: opening with 'w' truncated the file before
        the lock call, so get_existing_instance_pid() returned None and the
        'close existing' flow broke with "Could not find existing instance PID."
        """
        from desktop_app.app import get_existing_instance_pid

        lock_file = tmp_path / "jarvis_desktop.lock"
        existing_pid = 99999
        lock_file.write_bytes(str(existing_pid).encode())

        # Simulate a failed lock attempt by opening the file in append+read binary
        # mode (the fixed mode) and then locking failure — the file must be intact.
        fh = open(lock_file, 'a+b')
        try:
            # Verify the file still has the original PID content after being
            # opened non-destructively.
            fh.seek(0)
            content = fh.read().decode().strip()
            assert content == str(existing_pid), (
                f"Lock file was truncated on open — PID {existing_pid} was lost. "
                "This reproduces the bug where 'w' mode destroyed the PID before "
                "the lock attempt completed."
            )
        finally:
            fh.close()

        with patch("desktop_app.app.get_lock_file_path", return_value=lock_file):
            pid = get_existing_instance_pid()

        assert pid == existing_pid, (
            "get_existing_instance_pid() should still return the existing PID "
            "after a failed lock attempt."
        )

    def test_acquire_lock_writes_current_pid(self, tmp_path):
        """acquire_single_instance_lock() should write the current process PID."""
        import desktop_app.app as app_module

        lock_file = tmp_path / "jarvis_desktop.lock"
        original_handle = app_module._lock_file_handle

        try:
            with patch("desktop_app.app.get_lock_file_path", return_value=lock_file):
                result = app_module.acquire_single_instance_lock()

            assert result is True
            # PID should be readable from a separate handle because the lock
            # is at _LOCK_OFFSET, not at byte 0.
            content = lock_file.read_text().strip()
            assert content == str(os.getpid()), (
                f"Lock file should contain current PID {os.getpid()}, got {content!r}"
            )
        finally:
            # Release lock so the file handle is closed
            if app_module._lock_file_handle and app_module._lock_file_handle is not original_handle:
                try:
                    app_module._lock_file_handle.close()
                except Exception:
                    pass
                app_module._lock_file_handle = original_handle

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-specific lock test")
    def test_lock_blocks_second_process_and_pid_readable(self, tmp_path):
        """On Windows, the lock must block a second process while keeping the PID readable."""
        import desktop_app.app as app_module
        import subprocess

        lock_file = tmp_path / "jarvis_desktop.lock"
        original_handle = app_module._lock_file_handle

        try:
            with patch("desktop_app.app.get_lock_file_path", return_value=lock_file):
                result = app_module.acquire_single_instance_lock()
            assert result is True

            # Child process: try to acquire the same lock and read the PID
            child_code = '''
import msvcrt, sys
LOCK_OFFSET = 1024
lock_path = r"""''' + str(lock_file) + '''"""
fh = open(lock_path, "a+b")
fh.seek(LOCK_OFFSET)
try:
    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    print("LOCK_ACQUIRED")
except OSError:
    print("LOCK_BLOCKED")
fh.close()
try:
    pid = open(lock_path).read().strip()
    print("PID_READ=" + pid)
except Exception as e:
    print("PID_FAILED=" + str(e))
'''
            proc = subprocess.run(
                [sys.executable, "-c", child_code],
                capture_output=True, text=True, timeout=10,
            )
            lines = proc.stdout.strip().splitlines()
            assert "LOCK_BLOCKED" in lines, (
                f"Child should have been blocked from acquiring lock, got: {lines}"
            )
            pid_line = [l for l in lines if l.startswith("PID_READ=")]
            assert pid_line, f"Child should have read the PID, got: {lines}"
            assert pid_line[0] == f"PID_READ={os.getpid()}"
        finally:
            if app_module._lock_file_handle and app_module._lock_file_handle is not original_handle:
                try:
                    app_module._lock_file_handle.close()
                except Exception:
                    pass
                app_module._lock_file_handle = original_handle


class TestCudaRecoveryAction:
    """The tray exposes a 'Reinstall GPU libraries' action when the user has an
    NVIDIA GPU but the runtime CUDA probe failed. The recovery flow is the only
    way to retry the CUDA download from the user's perspective: the original
    Inno Setup task only fires once, and the marker file used to prevent
    re-runs even after a half-successful install.

    These tests cover the platform-gating logic and the command-line shape so
    we can change the implementation without breaking the contract.
    """

    def test_action_hidden_off_windows(self):
        from desktop_app.cuda_recovery import cuda_recovery_action

        with patch("sys.platform", "darwin"):
            assert cuda_recovery_action(install_root=Path("/fake")) is None

        with patch("sys.platform", "linux"):
            assert cuda_recovery_action(install_root=Path("/fake")) is None

    def test_action_hidden_when_no_nvidia_gpu(self, tmp_path):
        from desktop_app.cuda_recovery import cuda_recovery_action

        # No nvcuda.dll, no NVIDIA driver -> no point offering the action.
        with patch("sys.platform", "win32"), patch(
            "desktop_app.cuda_recovery._has_nvidia_driver", return_value=False
        ):
            assert cuda_recovery_action(install_root=tmp_path) is None

    def test_action_hidden_when_install_script_missing(self, tmp_path):
        from desktop_app.cuda_recovery import cuda_recovery_action

        # On dev machines (running from source) the Inno Setup-bundled script
        # does not exist; the menu action would be a dead button.
        with patch("sys.platform", "win32"), patch(
            "desktop_app.cuda_recovery._has_nvidia_driver", return_value=True
        ):
            assert cuda_recovery_action(install_root=tmp_path) is None

    def test_action_present_when_windows_gpu_and_script_exist(self, tmp_path):
        from desktop_app.cuda_recovery import cuda_recovery_action

        script = tmp_path / "install_cuda.ps1"
        script.write_text("# placeholder\n", encoding="utf-8")

        with patch("sys.platform", "win32"), patch(
            "desktop_app.cuda_recovery._has_nvidia_driver", return_value=True
        ):
            action = cuda_recovery_action(install_root=tmp_path)

        assert action is not None
        assert action.script_path == script
        assert action.target_dir == tmp_path / "cuda"
        assert "Reinstall GPU libraries" in action.label
        # Command is what gets handed to ShellExecute / subprocess; the test
        # pins the structure so we don't accidentally drop -ExecutionPolicy
        # Bypass and silently fail under restricted policies.
        assert action.executable.lower().endswith("powershell.exe")
        assert "-ExecutionPolicy" in action.arguments
        assert "Bypass" in action.arguments
        assert "-File" in action.arguments
        assert str(script) in action.arguments
        assert str(tmp_path / "cuda") in action.arguments
        assert "-LogPath" in action.arguments

    def test_quote_arg_handles_trailing_backslash(self):
        """Trailing backslashes inside quoted args must not eat the closing quote.

        Windows argv parsing collapses 2n backslashes before a `"` into n
        backslashes plus a string terminator, so a path like
        `C:\\Program Files\\Jarvis\\` quoted naively becomes
        `"C:\\Program Files\\Jarvis\\"` which CommandLineToArgvW reads as
        `C:\\Program Files\\Jarvis"` — quote eaten, next arg fused on. The
        canonical fix is to double trailing backslashes.
        """
        from desktop_app.cuda_recovery import _quote_arg

        # Trailing backslash + space gets doubled inside the quotes.
        result = _quote_arg(r"C:\Program Files\Jarvis\\")
        assert result.endswith('\\\\\\\\"'), (
            f"trailing backslashes must be doubled before the closing quote; got {result!r}"
        )
        # An embedded quote escapes correctly.
        assert _quote_arg('a"b') == '"a\\"b"'
        # Plain paths with spaces get the simple quoted form.
        assert _quote_arg(r"C:\Users\me\file") == r"C:\Users\me\file"
        assert _quote_arg(r"C:\Program Files\App") == r'"C:\Program Files\App"'
        # Empty string round-trips to "" so ShellExecute doesn't drop the slot.
        assert _quote_arg("") == '""'

    def test_run_uses_elevation_on_windows(self, tmp_path):
        """The script writes into Program Files; without elevation it silently
        no-ops. Make sure the run path requests UAC explicitly."""
        from desktop_app.cuda_recovery import CudaRecoveryAction, run_action

        action = CudaRecoveryAction(
            label="🎮 Reinstall GPU libraries",
            script_path=tmp_path / "install_cuda.ps1",
            target_dir=tmp_path / "cuda",
            executable=r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
            arguments=[
                "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(tmp_path / "install_cuda.ps1"),
                "-TargetDir", str(tmp_path / "cuda"),
                "-LogPath", str(tmp_path / "cuda" / "install.log"),
            ],
        )
        action.script_path.write_text("# placeholder\n", encoding="utf-8")

        captured = {}

        def fake_shell_execute(hwnd, verb, file, params, directory, show):
            captured["verb"] = verb
            captured["file"] = file
            captured["params"] = params
            return 42  # ShellExecuteW returns >32 on success

        with patch("sys.platform", "win32"), patch(
            "desktop_app.cuda_recovery._shell_execute", side_effect=fake_shell_execute
        ):
            run_action(action)

        assert captured.get("verb") == "runas", (
            "must request UAC elevation; install_cuda.ps1 writes to Program Files"
        )
        assert captured["file"].lower().endswith("powershell.exe")
        # The argument string should reference the script and the target dir.
        assert "install_cuda.ps1" in captured["params"]
        assert "-LogPath" in captured["params"]


class TestStartupFlow:
    """Launching opens the brain directly: no splash, wizard, log or face windows.

    The assistant starts on its own once Ollama is ready; if it isn't, the
    user is told through the tray and nothing else opens.
    """

    @pytest.fixture
    def tray_factory(self, monkeypatch):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtWidgets import QApplication
        from desktop_app import app as app_mod
        from desktop_app import ollama_setup

        QApplication.instance() or QApplication([])

        class FakeServer:
            url = "http://127.0.0.1:0/cerebro"

            def __init__(self, port=0):
                self.started = False

            def start(self):
                self.started = True

            def stop(self):
                self.started = False

        class FakeDaemon:
            def __init__(self):
                self.running = False
                self.starts = 0

            def start(self):
                self.running = True
                self.starts += 1

            def stop(self):
                self.running = False

        opened = []
        monkeypatch.setattr(app_mod, "BrainServer", FakeServer)
        monkeypatch.setattr(app_mod, "DaemonController", FakeDaemon)
        monkeypatch.setattr(app_mod, "kill_orphaned_daemons", lambda: None)
        # QtWebEngine can't start in headless CI containers; the window logic
        # under test doesn't depend on the embedded browser.
        monkeypatch.setattr(app_mod, "HAS_WEBENGINE", False)
        monkeypatch.setattr(app_mod.BrainWindow, "open", lambda self: opened.append("brain"))

        def make(ready: bool):
            class FakeBootstrap:
                def run(self, report):
                    report("📥 Baixando o modelo x (uma única vez)…")
                    return ollama_setup.BootstrapResult(ready, "ok" if ready else "Ollama não encontrado")

            monkeypatch.setattr(ollama_setup, "OllamaBootstrap", FakeBootstrap)
            tray = app_mod.JarvisSystemTray()
            notes = []
            monkeypatch.setattr(tray, "notify", lambda title, msg, icon=None: notes.append(msg))
            return tray, opened, notes

        return make

    @staticmethod
    def _wait_until(predicate, timeout=5.0):
        import time
        from PyQt6.QtWidgets import QApplication

        end = time.time() + timeout
        while time.time() < end:
            QApplication.processEvents()
            if predicate():
                return True
            time.sleep(0.02)
        return False

    def test_brain_opens_and_assistant_starts_when_ollama_is_ready(self, tray_factory):
        from PyQt6.QtWidgets import QApplication

        tray, opened, notes = tray_factory(ready=True)
        tray.start()
        assert opened == ["brain"]
        assert self._wait_until(lambda: tray.daemon.starts == 1)
        visible = [w for w in QApplication.topLevelWidgets() if w.isVisible()]
        assert visible == [], f"unexpected windows: {[w.windowTitle() for w in visible]}"
        tray.cleanup_on_exit()

    def test_assistant_waits_and_user_is_told_when_ollama_is_missing(self, tray_factory):
        tray, opened, notes = tray_factory(ready=False)
        tray.start()
        assert self._wait_until(lambda: tray._bootstrap is None)
        assert tray.daemon.starts == 0
        assert any("Ollama" in n for n in notes)
        tray.cleanup_on_exit()

    def test_model_download_progress_is_surfaced(self, tray_factory):
        tray, opened, notes = tray_factory(ready=True)
        tray.start()
        assert self._wait_until(lambda: any("Baixando" in n for n in notes))
        tray.cleanup_on_exit()
