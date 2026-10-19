"""
Jarvis Desktop App: system tray + the brain (cérebro) window.

Start-up shows no intermediate screens: the tray icon appears, the brain
window opens, Ollama is prepared silently in the background (server start
and model downloads, progress in the terminal and tray notifications), and
the voice assistant starts listening as soon as it is ready.
"""

from __future__ import annotations

import os
import sys

# Fix OpenBLAS threading crash in bundled apps.
# Must be set before numpy is imported (via faster-whisper, etc.).
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")

import warnings

# Suppress pkg_resources deprecation warning from webrtcvad
warnings.filterwarnings("ignore", message="pkg_resources is deprecated", category=UserWarning)

import atexit
import signal
import subprocess
import threading
import time
import traceback
import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import psutil
from PyQt6.QtCore import QObject, Qt, QThread, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication, QDialog, QMainWindow, QMenu, QSystemTrayIcon, QVBoxLayout, QWidget

if TYPE_CHECKING:
    from desktop_app.cuda_recovery import CudaRecoveryAction

try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView

    HAS_WEBENGINE = True
except ImportError:
    QWebEngineView = None  # type: ignore[assignment,misc]
    HAS_WEBENGINE = False

from jarvis.config import _default_db_path, default_config_path, load_settings
from jarvis.debug import debug_log
from desktop_app.brain_server import BrainServer
from desktop_app.themes import COLORS, JARVIS_THEME_STYLESHEET

# Global lock file handle (must remain open for the lock to persist)
_lock_file_handle = None
# Byte offset used for the lock region — deliberately beyond where PID content
# lives (bytes 0–~10) so other processes can still read the PID while the lock
# is held. Windows msvcrt.locking() creates mandatory locks that block ALL
# access (including reads from other handles) on the locked bytes.
_LOCK_OFFSET = 1024

# Must exceed SHUTDOWN_DIARY_TIMEOUT_SEC (45 s) in daemon.py so the final
# memory/diary update can finish before the daemon is force-stopped.
_SHUTDOWN_WAIT_SEC = 60


# ─────────────────────────────────────────────────────────────────────────────
# Crash logging (bundled app) and single-instance lock
# ─────────────────────────────────────────────────────────────────────────────


def get_crash_paths() -> tuple[Path, Path, Path]:
    """Paths for the crash log, crash marker and previous crash log."""
    from desktop_app.paths import get_log_dir

    log_dir = get_log_dir()
    return (
        log_dir / "jarvis_desktop_crash.log",
        log_dir / ".crash_marker",
        log_dir / "previous_crash.log",
    )


def setup_crash_logging() -> Optional[Path]:
    """Bundled app only: send stdout/stderr and fatal tracebacks to a log file."""
    if not getattr(sys, "frozen", False):
        return None
    crash_log, _, _ = get_crash_paths()
    try:
        crash_log.parent.mkdir(parents=True, exist_ok=True)
        handle = open(crash_log, "w", encoding="utf-8", buffering=1)
        sys.stdout = handle
        sys.stderr = handle
        import faulthandler

        faulthandler.enable(file=handle)
        print(f"📁 Jarvis log: {crash_log}", flush=True)
        return crash_log
    except OSError:
        return None


def get_lock_file_path() -> Path:
    """Path of the single-instance lock file."""
    if sys.platform == "darwin":
        lock_dir = Path.home() / "Library" / "Application Support" / "Jarvis"
    elif sys.platform == "win32":
        lock_dir = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Jarvis"
    else:
        lock_dir = Path.home() / ".jarvis"
    lock_dir.mkdir(parents=True, exist_ok=True)
    return lock_dir / "jarvis_desktop.lock"


def get_existing_instance_pid() -> Optional[int]:
    """PID of the running Jarvis instance, read from the lock file."""
    try:
        content = get_lock_file_path().read_text().strip()
        return int(content) if content.isdigit() else None
    except OSError:
        return None


def kill_existing_instance(pid: int) -> bool:
    """Terminate a previous Jarvis instance. True when it is gone."""
    try:
        process = psutil.Process(pid)
        name = process.name().lower()
        if "jarvis" not in name and "python" not in name:
            debug_log(f"PID {pid} doesn't look like Jarvis ({name}), not killing", "desktop")
            return False
        process.terminate()
        try:
            process.wait(timeout=5)
        except psutil.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
        return True
    except psutil.NoSuchProcess:
        return True
    except Exception as e:
        debug_log(f"failed to kill process {pid}: {e}", "desktop")
        return False


def acquire_single_instance_lock() -> bool:
    """Lock so only one desktop app runs. True when we hold the lock."""
    global _lock_file_handle
    lock_file = get_lock_file_path()
    try:
        # Append+read binary: never truncates, so a running instance's PID stays readable.
        _lock_file_handle = open(lock_file, "a+b")
        if sys.platform == "win32":
            import msvcrt

            _lock_file_handle.seek(_LOCK_OFFSET)
            try:
                msvcrt.locking(_lock_file_handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                _lock_file_handle.close()
                _lock_file_handle = None
                return False
        else:
            import fcntl

            try:
                fcntl.flock(_lock_file_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                _lock_file_handle.close()
                _lock_file_handle = None
                return False

        _lock_file_handle.seek(0)
        _lock_file_handle.truncate(0)
        _lock_file_handle.write(str(os.getpid()).encode())
        _lock_file_handle.flush()

        def release_lock() -> None:
            global _lock_file_handle
            if _lock_file_handle:
                try:
                    _lock_file_handle.close()
                except OSError:
                    pass
                _lock_file_handle = None

        atexit.register(release_lock)
        return True
    except Exception as e:
        print(f"⚠️ Could not acquire single-instance lock: {e}", flush=True)
        return True  # fail open


# ─────────────────────────────────────────────────────────────────────────────
# Voice assistant daemon
# ─────────────────────────────────────────────────────────────────────────────


class _DaemonThread(QThread):
    """Bundled app: the daemon runs in-process (Qt-safe thread)."""

    def run(self) -> None:
        try:
            from jarvis.daemon import is_stop_requested, main as daemon_main

            daemon_main()
            if is_stop_requested():
                print("✅ Daemon stopped gracefully", flush=True)
            else:
                print("⚠️ Daemon exited unexpectedly", flush=True)
        except Exception as e:
            print(f"❌ Daemon error: {e}\n{traceback.format_exc()}", flush=True)
            debug_log(f"daemon thread error: {e}", "desktop")


class DaemonController:
    """Starts/stops the voice assistant: in-process when bundled, a subprocess from source.

    From source the daemon inherits the terminal, so its logs appear exactly
    where the app was launched.
    """

    def __init__(self) -> None:
        self.bundled = bool(getattr(sys, "frozen", False))
        self._thread: Optional[_DaemonThread] = None
        self._process: Optional[subprocess.Popen] = None

    @property
    def running(self) -> bool:
        if self._thread is not None:
            return not self._thread.isFinished()
        return self._process is not None and self._process.poll() is None

    def start(self) -> None:
        if self.running:
            return
        if self.bundled:
            # daemon.main() clears any previous stop request itself.
            self._thread = _DaemonThread()
            self._thread.start()
        else:
            env = os.environ.copy()
            src_path = str(Path(__file__).resolve().parent.parent)
            env["PYTHONPATH"] = src_path + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
            creationflags = 0
            if sys.platform == "win32":
                creationflags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
            self._process = subprocess.Popen(
                [sys.executable, "-m", "jarvis.main"], stdin=subprocess.PIPE, env=env, creationflags=creationflags,
            )
        debug_log("daemon started from desktop app", "desktop")

    def stop(self) -> None:
        """Graceful stop (the daemon saves memory on the way out), then force after a timeout."""
        if self._thread is not None:
            from jarvis.daemon import request_stop

            request_stop()
            if not self._thread.wait(_SHUTDOWN_WAIT_SEC * 1000):
                debug_log("daemon thread did not stop in time", "desktop")
            self._thread = None
        elif self._process is not None:
            if self._process.poll() is None:
                if sys.platform == "win32":
                    try:
                        if self._process.stdin:
                            self._process.stdin.close()
                        self._process.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
                    except OSError:
                        pass
                else:
                    self._process.send_signal(signal.SIGINT)
                try:
                    self._process.wait(timeout=_SHUTDOWN_WAIT_SEC)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait()
            self._process = None
        debug_log("daemon stopped from desktop app", "desktop")


def kill_orphaned_daemons() -> None:
    """Terminate daemons left behind by a previous session."""
    current = os.getpid()
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            cmdline = proc.info.get("cmdline") or []
            if proc.pid != current and "jarvis.main" in " ".join(cmdline):
                debug_log(f"killing orphaned jarvis process: {proc.pid}", "desktop")
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except psutil.TimeoutExpired:
                    proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue


# ─────────────────────────────────────────────────────────────────────────────
# Windows
# ─────────────────────────────────────────────────────────────────────────────


class BrainWindow(QMainWindow):
    """The brain (cérebro) page in an embedded web view (system browser as fallback)."""

    def __init__(self, url: str) -> None:
        super().__init__()
        self._url = url
        self.setWindowTitle("🧠 Cérebro do J.A.R.V.I.S.")
        self.resize(1280, 860)
        self.setStyleSheet(JARVIS_THEME_STYLESHEET)
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        # macOS bundles can't host QtWebEngine reliably; use the browser there.
        is_macos_bundle = sys.platform == "darwin" and getattr(sys, "frozen", False)
        self._view = None
        if HAS_WEBENGINE and not is_macos_bundle:
            try:
                self._view = QWebEngineView()
                self._view.page().setBackgroundColor(QColor(COLORS["graph_bg"]))
                layout.addWidget(self._view)
            except Exception as e:
                debug_log(f"web view unavailable: {e}", "desktop")
                self._view = None

    def open(self) -> None:
        if self._view is None:
            webbrowser.open(self._url)
            return
        if self._view.url().isEmpty():
            self._view.setUrl(QUrl(self._url))
        self.show()
        self.raise_()
        self.activateWindow()


class _BootstrapWorker(QThread):
    """Prepares Ollama in the background and reports progress."""

    progress = pyqtSignal(str)
    done = pyqtSignal(bool, str)

    def run(self) -> None:
        from desktop_app.ollama_setup import OllamaBootstrap

        try:
            result = OllamaBootstrap().run(report=self.progress.emit)
            self.done.emit(result.ready, result.message)
        except Exception as e:
            debug_log(f"ollama bootstrap failed: {e}", "desktop")
            self.done.emit(False, f"Falha ao preparar o Ollama: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Tray
# ─────────────────────────────────────────────────────────────────────────────


class JarvisSystemTray(QObject):
    """Tray icon + menu; owns the brain server/window and the daemon."""

    def __init__(self, brain_port: int = 5050) -> None:
        super().__init__()
        self.app = QApplication.instance() or QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)

        kill_orphaned_daemons()
        self.daemon = DaemonController()
        self.brain_server = BrainServer(port=brain_port)
        self.brain_server.start()
        self.brain_window = BrainWindow(self.brain_server.url)
        self._bootstrap: Optional[_BootstrapWorker] = None
        self._was_running = False

        self.tray_icon = QSystemTrayIcon()
        self._build_menu()
        self._refresh()
        self.tray_icon.show()

        self.status_timer = QTimer()
        self.status_timer.timeout.connect(self._watch_daemon)
        self.status_timer.start(2000)
        self.app.aboutToQuit.connect(self.cleanup_on_exit)
        debug_log("desktop app initialized", "desktop")

    # -- menu ---------------------------------------------------------------

    def _build_menu(self) -> None:
        self.menu = QMenu()
        self.brain_action = QAction("🧠 Abrir Cérebro")
        self.brain_action.triggered.connect(self.brain_window.open)
        self.menu.addAction(self.brain_action)
        self.toggle_action = QAction("▶️ Retomar escuta")
        self.toggle_action.triggered.connect(self.toggle_listening)
        self.menu.addAction(self.toggle_action)
        self.menu.addSeparator()
        self.settings_action = QAction("⚙️ Configurações")
        self.settings_action.triggered.connect(self.show_settings)
        self.menu.addAction(self.settings_action)
        self._maybe_add_cuda_recovery_action()
        self.menu.addSeparator()
        self.vault_action = QAction("🟣 Abrir cofre do Obsidian")
        self.vault_action.triggered.connect(self.open_vault_directory)
        self.menu.addAction(self.vault_action)
        self.config_action = QAction("📁 Pasta de configuração")
        self.config_action.triggered.connect(lambda: self.open_directory(default_config_path().parent))
        self.menu.addAction(self.config_action)
        self.data_action = QAction("💾 Pasta de dados")
        self.data_action.triggered.connect(lambda: self.open_directory(Path(_default_db_path()).parent))
        self.menu.addAction(self.data_action)
        self.menu.addSeparator()
        self.status_action = QAction("⚪ Preparando…")
        self.status_action.setEnabled(False)
        self.menu.addAction(self.status_action)
        self.menu.addSeparator()
        self.quit_action = QAction("🚪 Sair")
        self.quit_action.triggered.connect(self.quit_app)
        self.menu.addAction(self.quit_action)
        self.tray_icon.setContextMenu(self.menu)
        self.tray_icon.activated.connect(self._on_tray_activated)

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.brain_window.open()

    def _maybe_add_cuda_recovery_action(self) -> None:
        """Windows + NVIDIA only: reinstall GPU libraries for Whisper."""
        try:
            from desktop_app.cuda_recovery import cuda_recovery_action
        except Exception as e:
            debug_log(f"cuda recovery import failed: {e}", "desktop")
            return
        if getattr(sys, "frozen", False):
            install_root = Path(sys.executable).parent
        else:
            install_root = Path(__file__).resolve().parents[2] / "installer" / "windows"
        spec = cuda_recovery_action(install_root=install_root)
        if spec is None:
            return
        self.cuda_recovery_action = QAction(spec.label)
        self.cuda_recovery_action.triggered.connect(lambda: self._run_cuda_recovery(spec))
        self.menu.addAction(self.cuda_recovery_action)

    def _run_cuda_recovery(self, spec: "CudaRecoveryAction") -> None:
        from desktop_app.cuda_recovery import run_action

        if run_action(spec):
            self.notify("Bibliotecas de GPU", "Instalação iniciada. Reinicie o JARVIS ao terminar.")
        else:
            self.notify("Bibliotecas de GPU", f"Não foi possível iniciar. Veja {spec.target_dir / 'install.log'}",
                        QSystemTrayIcon.MessageIcon.Warning)

    # -- state ----------------------------------------------------------------

    def notify(self, title: str, message: str,
               icon: QSystemTrayIcon.MessageIcon = QSystemTrayIcon.MessageIcon.Information) -> None:
        self.tray_icon.showMessage(title, message, icon, 4000)

    def _refresh(self, status: Optional[str] = None) -> None:
        listening = self.daemon.running
        self.toggle_action.setText("⏸️ Pausar escuta" if listening else "▶️ Retomar escuta")
        if status:
            self.status_action.setText(status)
        elif listening:
            self.status_action.setText("🟢 Ouvindo")
        elif self._bootstrap is None:
            self.status_action.setText("⚪ Pausado")
        self.tray_icon.setIcon(self._icon(listening))
        self.tray_icon.setToolTip("J.A.R.V.I.S. " + ("ouvindo" if listening else "pausado"))

    def _icon(self, listening: bool) -> QIcon:
        path = Path(__file__).parent / "desktop_assets" / ("icon_listening.png" if listening else "icon_idle.png")
        if path.exists():
            return QIcon(str(path))
        pixmap = QPixmap(64, 64)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        colour = QColor(COLORS["accent_primary"] if listening else COLORS["text_muted"])
        painter.setBrush(colour)
        painter.setPen(colour)
        painter.drawEllipse(4, 4, 56, 56)
        painter.setPen(Qt.GlobalColor.white)
        painter.setFont(QFont("Arial", 32, QFont.Weight.Bold))
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "J")
        painter.end()
        return QIcon(pixmap)

    def _watch_daemon(self) -> None:
        running = self.daemon.running
        if self._was_running and not running:
            self.notify("J.A.R.V.I.S.", "O assistente parou inesperadamente.", QSystemTrayIcon.MessageIcon.Warning)
            debug_log("daemon ended unexpectedly", "desktop")
        self._was_running = running
        self._refresh()

    # -- start-up -------------------------------------------------------------

    def start(self) -> None:
        """Open the brain and prepare Ollama; the daemon starts when it is ready."""
        self.brain_window.open()
        self._refresh("⏳ Preparando o Ollama…")
        self._bootstrap = _BootstrapWorker()
        self._bootstrap.progress.connect(self._on_bootstrap_progress)
        self._bootstrap.done.connect(self._on_bootstrap_done)
        self._bootstrap.start()

    def _on_bootstrap_progress(self, message: str) -> None:
        print(f"  {message}", flush=True)
        if message.startswith(("📥", "🚀")):
            self.notify("J.A.R.V.I.S.", message)
            self._refresh(message[:60])

    def _on_bootstrap_done(self, ready: bool, message: str) -> None:
        self._bootstrap = None
        if not ready:
            print(f"❌ {message}", flush=True)
            self.notify("J.A.R.V.I.S.", message, QSystemTrayIcon.MessageIcon.Critical)
            self._refresh("❌ Ollama indisponível")
            return
        print("✅ Ollama pronto, iniciando o assistente…", flush=True)
        self.start_daemon()

    # -- actions --------------------------------------------------------------

    def start_daemon(self) -> None:
        try:
            self.daemon.start()
            self._was_running = True
            self._refresh()
            self.notify("J.A.R.V.I.S.", "Ouvindo. Diga \"Jarvis\" para falar comigo.")
        except Exception as e:
            debug_log(f"failed to start daemon: {e}", "desktop")
            print(f"❌ Falha ao iniciar o assistente: {e}", flush=True)
            self.notify("J.A.R.V.I.S.", f"Falha ao iniciar: {e}", QSystemTrayIcon.MessageIcon.Critical)

    def stop_daemon(self) -> None:
        self._refresh("💾 Salvando memória…")
        self.app.processEvents()
        self._was_running = False
        self.daemon.stop()
        self._refresh()

    def toggle_listening(self) -> None:
        if self.daemon.running:
            self.stop_daemon()
        else:
            self.start_daemon()

    def show_settings(self) -> None:
        from desktop_app.settings_window import SettingsWindow

        if SettingsWindow().exec() == QDialog.DialogCode.Accepted and self.daemon.running:
            self.stop_daemon()
            self.start_daemon()

    def open_directory(self, path: Path) -> None:
        try:
            path.mkdir(parents=True, exist_ok=True)
            if sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            elif sys.platform == "win32":
                os.startfile(str(path))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except OSError as e:
            debug_log(f"failed to open {path}: {e}", "desktop")
            self.notify("J.A.R.V.I.S.", f"Não foi possível abrir {path}", QSystemTrayIcon.MessageIcon.Warning)

    def open_vault_directory(self) -> None:
        try:
            vault = Path(load_settings().obsidian_vault_path)
        except Exception:
            vault = Path.home() / "Obsidian" / "JARVIS"
        self.open_directory(vault)

    def cleanup_on_exit(self) -> None:
        debug_log("cleaning up on exit", "desktop")
        if self.daemon.running:
            self.daemon.stop()
        self.brain_server.stop()

    def quit_app(self) -> None:
        if self.daemon.running:
            self.stop_daemon()
        self.tray_icon.hide()
        self.app.quit()

    def run(self) -> int:
        return self.app.exec()


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────


def main() -> int:
    """Desktop app entry point."""
    if sys.platform == "win32" and not getattr(sys, "frozen", False):
        try:
            import io

            if hasattr(sys.stdout, "buffer"):
                sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
            if hasattr(sys.stderr, "buffer"):
                sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
        except Exception:
            pass

    # Required for PyInstaller: prevents bundled apps spawning copies of themselves.
    import multiprocessing

    multiprocessing.freeze_support()

    if not acquire_single_instance_lock():
        # A previous instance is running: replace it instead of asking.
        pid = get_existing_instance_pid()
        print(f"🔄 Encerrando a instância anterior do JARVIS (PID {pid})…", flush=True)
        if not pid or not kill_existing_instance(pid):
            print("❌ Não foi possível encerrar a instância anterior.", flush=True)
            return 1
        time.sleep(0.5)
        if not acquire_single_instance_lock():
            print("❌ Não foi possível obter o bloqueio de instância única.", flush=True)
            return 1

    setup_crash_logging()
    print("🤖 Iniciando o J.A.R.V.I.S.…", flush=True)

    tray: Optional[JarvisSystemTray] = None

    def on_signal(signum: int, frame: object) -> None:
        print(f"👋 Sinal {signum} recebido, encerrando…", flush=True)
        if tray is not None:
            tray.cleanup_on_exit()
        sys.exit(0)

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    try:
        app = QApplication.instance() or QApplication(sys.argv)
        app.setQuitOnLastWindowClosed(False)
        tray = JarvisSystemTray()
        tray.start()
        return tray.run()
    except Exception as e:
        print(f"❌ Erro fatal no app: {e}\n{traceback.format_exc()}", flush=True)
        debug_log(f"desktop app fatal error: {e}", "desktop")
        return 1


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    sys.exit(main())
