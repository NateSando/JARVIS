# Desktop App Specification

The Jarvis desktop app is a cross-platform PyQt6 system tray application around the voice assistant. Its one window is the brain (cérebro): the Obsidian vault rendered as a living graph.

## Overview

The desktop app is a **separate package** from the core `jarvis` module. It depends on `jarvis`; `jarvis` has no knowledge of or dependency on the desktop app. This keeps PyQt6 out of the core and lets Jarvis run headless (`python -m jarvis.main`).

Design rule: **launch opens straight into the brain**. No splash screen, setup wizard, model prompt, crash-report dialog, log window or face window. Anything that needs the user's attention is a tray notification plus a terminal line.

## Package Structure

```
src/desktop_app/
├── __init__.py          # Package exports, main() entry point
├── app.py               # JarvisSystemTray, BrainWindow, DaemonController, start-up flow
├── brain_server.py      # Local Flask server for /cerebro and /api/cerebro (127.0.0.1 only)
├── brain_page.py        # Brain page HTML (offline canvas), see jarvis/brain/brain.spec.md
├── ollama_setup.py      # Silent Ollama bootstrap (CLI check, server start, model pulls)
├── settings_window.py   # Auto-generated settings UI from config metadata
├── cuda_recovery.py     # Windows + NVIDIA: reinstall GPU libraries
├── themes.py            # Qt stylesheet and colour palette (incl. graph_* brain colours)
├── paths.py             # Shared log directory
└── desktop_assets/      # Icons
```

## Start-up Flow

```
main()
 ├─ single-instance lock (a second launch terminates the first, no dialog)
 ├─ crash logging to file (bundled app only)
 └─ JarvisSystemTray
     ├─ kill orphaned daemons from a previous session
     ├─ start BrainServer (127.0.0.1:5050)
     ├─ tray icon + menu
     └─ start()
         ├─ BrainWindow.open()                     🧠 visible immediately
         └─ _BootstrapWorker (QThread) → OllamaBootstrap.run()
              ├─ CLI missing      → tray: how to install Ollama; daemon not started
              ├─ server stopped   → start it, wait ≤ 20 s
              ├─ models missing   → `ollama pull` each, progress to terminal + tray
              └─ ready            → DaemonController.start()  🎤 listening
```

`OllamaBootstrap` takes every side effect (find CLI, server probe, start, list, pull, sleep) as an injectable callable so the flow is unit-tested without Ollama. Required models: chat, embedding and intent-judge models from config.

## Main Components

### JarvisSystemTray

Tray menu (pt-BR):

| Item | Action |
|------|--------|
| 🧠 Abrir Cérebro | Show the brain window (also on tray-icon click) |
| ⏸️ Pausar escuta / ▶️ Retomar escuta | Stop/start the daemon |
| ⚙️ Configurações | Settings window; saving restarts a running daemon |
| 🎮 Reinstall GPU libraries | Windows + NVIDIA only (see below) |
| 🟣 Abrir cofre do Obsidian | Open `obsidian_vault_path` in the file manager |
| 📁 Pasta de configuração / 💾 Pasta de dados | Open config / data folders |
| status line | Preparando / Ouvindo / Pausado / Ollama indisponível |
| 🚪 Sair | Graceful stop, then quit |

A 2-second timer watches the daemon and notifies if it ends unexpectedly.

### BrainWindow

Embeds the brain page in a `QWebEngineView` with the `graph_bg` theme colour. macOS bundled builds (QtWebEngine sandbox issues) and installs without WebEngine open the page in the system browser instead.

### BrainServer

`desktop_app/brain_server.py`: Flask app on a background thread via `werkzeug.make_server`, bound to `127.0.0.1` only. Routes: `/` → redirect to `/cerebro`, `/cerebro` (page), `/api/cerebro` (idempotent vault sync + brain map). Errors return a generic message; details go to `debug_log`.

### Tray Menu: GPU Library Recovery (Windows)

`cuda_recovery.py` exposes the `🎮 Reinstall GPU libraries` action, added only on Windows with an NVIDIA driver and the bundled `install_cuda.ps1` present. It re-runs the script elevated (UAC) and reports through a tray notification. The Inno Setup `VerifyCudaInstall` hook still checks the `.cuda_installed` marker after install.

## Daemon Integration

`DaemonController` runs `jarvis.daemon.main()`:

- **Bundled**: in a `QThread` in the same process. Stop = `request_stop()`, then wait.
- **From source**: `python -m jarvis.main` subprocess inheriting the terminal, so its logs appear where the app was launched. Stop = SIGINT (Windows: close stdin + CTRL_BREAK).

Both wait up to 60 s (longer than the daemon's 45 s shutdown diary update) before forcing.

## Theme System

All Qt UI uses `themes.py` (`COLORS`, `JARVIS_THEME_STYLESHEET`). The brain page reads its colours from the same `COLORS` (`graph_bg`, `graph_link`, `graph_node`, `accent_primary`).

## Error Handling

- **Crash logs**: bundled apps redirect stdout/stderr and `faulthandler` to `jarvis_desktop_crash.log`.
- **No Ollama**: tray notification with the install link; the brain still opens.
- **No WebEngine**: brain opens in the system browser.
- **Fatal start-up error**: printed and logged; exit code 1.

## Platform-Specific Behaviour

| Feature | macOS | Windows | Linux |
|---------|-------|---------|-------|
| Tray icon | Native menu bar | System tray | System tray |
| Ollama start | `open -a Ollama` | `ollama serve` (hidden) | `ollama serve` |
| Crash logs | `~/Library/Logs/Jarvis` | `%LOCALAPPDATA%\Jarvis` | `~/.jarvis` |
| Brain window | System browser* | Embedded WebEngine | Embedded WebEngine |

*macOS bundled apps use the system browser due to QtWebEngine sandbox issues.

## File Locations

| File | macOS | Windows | Linux |
|------|-------|---------|-------|
| Config | `~/.config/jarvis/` | `%APPDATA%\jarvis\` | `~/.config/jarvis/` |
| Database | `~/.local/share/jarvis/` | `%LOCALAPPDATA%\jarvis\` | `~/.local/share/jarvis/` |
| Obsidian vault | `~/Obsidian/JARVIS` (configurable) | same | same |
| Crash logs | `~/Library/Logs/Jarvis/` | `%LOCALAPPDATA%\Jarvis\` | `~/.jarvis/` |
| Instance lock | `~/Library/Application Support/Jarvis/` | `%LOCALAPPDATA%\Jarvis\` | `~/.jarvis/` |
