"""
Jarvis Desktop App: system tray + the brain (cérebro) window.

Supports Windows, Ubuntu (Linux), and macOS.
"""

from __future__ import annotations

import os

# Fix OpenBLAS threading crash in bundled apps
# Must be set before numpy is imported (via faster-whisper, etc.)
os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
os.environ.setdefault('MKL_NUM_THREADS', '1')
os.environ.setdefault('OMP_NUM_THREADS', '1')

# Re-export main for entry point
from desktop_app.app import main, JarvisSystemTray, acquire_single_instance_lock, get_crash_paths

__all__ = ['main', 'JarvisSystemTray', 'acquire_single_instance_lock', 'get_crash_paths']
