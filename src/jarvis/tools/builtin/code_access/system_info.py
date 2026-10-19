"""systemInfo: read-only operating-system snapshot, only when the user enabled it.

Deliberately reports names and numbers only: no command lines, environment
variables, user names or network addresses, since those routinely carry
secrets or personal data.
"""

from __future__ import annotations

import os
import platform
import sys
import time
from typing import Any, Callable, Dict, Optional

from ....debug import debug_log
from ...base import Tool, ToolContext
from ...types import ToolExecutionResult

_NOT_ENABLED = (
    "System access is not enabled. The user can allow read-only system information by turning on "
    "'system_access_enabled' in the settings (Code & System Access) or config.json."
)
SECTIONS = ("overview", "processes", "disks")
_TOP_PROCESSES = 10


def _gb(n: float) -> str:
    return f"{n / 1024 ** 3:.1f} GB"


def _overview() -> str:
    import psutil

    vm = psutil.virtual_memory()
    swap = psutil.swap_memory()
    uptime_h = (time.time() - psutil.boot_time()) / 3600
    freq = psutil.cpu_freq()
    lines = [
        f"OS: {platform.system()} {platform.release()} ({platform.version()[:80]})",
        f"Architecture: {platform.machine()}",
        f"CPU: {platform.processor() or platform.machine()}, {psutil.cpu_count(logical=False) or '?'} cores / "
        f"{psutil.cpu_count()} threads" + (f", {freq.current:.0f} MHz" if freq else ""),
        f"CPU usage: {psutil.cpu_percent(interval=0.3):.0f}%",
        f"RAM: {_gb(vm.used)} used of {_gb(vm.total)} ({vm.percent:.0f}%)",
        f"Swap: {_gb(swap.used)} used of {_gb(swap.total)}",
        f"Uptime: {uptime_h:.1f} h",
        f"Python: {sys.version.split()[0]}",
    ]
    if hasattr(os, "getloadavg"):
        load = os.getloadavg()
        lines.append(f"Load average: {load[0]:.2f} {load[1]:.2f} {load[2]:.2f}")
    return "\n".join(lines)


def _processes() -> str:
    import psutil

    procs = list(psutil.process_iter(["pid", "name", "memory_info"]))
    for p in procs:
        try:
            p.cpu_percent(None)
        except (psutil.Error, OSError):
            pass
    time.sleep(0.3)
    rows: list[tuple[float, float, int, str]] = []
    for p in procs:
        try:
            mem = (p.info.get("memory_info").rss if p.info.get("memory_info") else 0) / 1024 ** 2
            rows.append((p.cpu_percent(None), mem, p.info["pid"], p.info.get("name") or "?"))
        except (psutil.Error, OSError):
            continue
    by_cpu = sorted(rows, key=lambda r: -r[0])[:_TOP_PROCESSES]
    by_mem = sorted(rows, key=lambda r: -r[1])[:_TOP_PROCESSES]

    def table(title: str, data: list[tuple[float, float, int, str]]) -> list[str]:
        return [title, "PID | CPU% | MEM MB | name"] + [f"{pid} | {cpu:.0f} | {mem:.0f} | {name}" for cpu, mem, pid, name in data]

    return "\n".join([f"Processes running: {len(rows)}"] + table("Top by CPU:", by_cpu) + table("Top by memory:", by_mem))


def _disks() -> str:
    import psutil

    lines = ["Mount | used / total | %"]
    seen: set[str] = set()
    for part in psutil.disk_partitions(all=False):
        if part.mountpoint in seen:
            continue
        seen.add(part.mountpoint)
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except (PermissionError, OSError):
            continue
        lines.append(f"{part.mountpoint} ({part.fstype}) | {_gb(usage.used)} / {_gb(usage.total)} | {usage.percent:.0f}%")
    if len(lines) == 1:
        usage = psutil.disk_usage(os.path.abspath(os.sep))
        lines.append(f"{os.path.abspath(os.sep)} | {_gb(usage.used)} / {_gb(usage.total)} | {usage.percent:.0f}%")
    return "\n".join(lines)


_HANDLERS: dict[str, Callable[[], str]] = {"overview": _overview, "processes": _processes, "disks": _disks}


class SystemInfoTool(Tool):
    """Read-only OS, hardware, process and disk snapshot (opt-in)."""

    @property
    def name(self) -> str:
        return "systemInfo"

    @property
    def description(self) -> str:
        return (
            "Read-only snapshot of the user's computer when they enabled system access: operating system, CPU, "
            "memory and load (overview), heaviest running processes (processes), or disk usage (disks). "
            "Use for questions about the machine's health, performance, resources or what is running."
        )

    @property
    def inputSchema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "section": {"type": "string", "enum": list(SECTIONS), "description": "overview | processes | disks"},
            },
            "required": [],
        }

    def run(self, args: Optional[Dict[str, Any]], context: ToolContext) -> ToolExecutionResult:
        if not getattr(context.cfg, "system_access_enabled", False):
            debug_log("systemInfo refused: system access disabled", "tools")
            return ToolExecutionResult(success=False, reply_text=_NOT_ENABLED)
        section = str((args or {}).get("section") or "overview").strip().lower()
        handler = _HANDLERS.get(section)
        if handler is None:
            return ToolExecutionResult(success=False, reply_text=f"Unknown section '{section}'. Use one of: {', '.join(SECTIONS)}.")
        context.user_print(f"🖥️ Reading system info ({section})")
        try:
            text = handler()
        except ImportError:
            return ToolExecutionResult(success=False, reply_text="psutil is not installed, so system information is unavailable.")
        except Exception as e:  # psutil raises platform-specific errors
            debug_log(f"systemInfo {section} failed: {e}", "tools")
            return ToolExecutionResult(success=False, reply_text=f"Could not read system {section}: {e}")
        debug_log(f"systemInfo {section} ok ({len(text)} chars)", "tools")
        return ToolExecutionResult(success=True, reply_text=text)
