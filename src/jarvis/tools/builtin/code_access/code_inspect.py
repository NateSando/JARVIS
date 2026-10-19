"""codeInspect: read-only view of the user's code, limited to folders they granted."""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from ....debug import debug_log
from ...base import Tool, ToolContext
from ...types import ToolExecutionResult
from . import scanner
from .policy import AccessDenied, AccessPolicy

_NOT_GRANTED = (
    "Code access is not enabled: the user has not granted any folder. "
    "They can grant read-only access by adding project folders to "
    "'code_access_roots' in the settings (Code & System Access) or config.json."
)

OPERATIONS = ("overview", "tree", "search", "read", "outline", "roots")


class CodeInspectTool(Tool):
    """Read-only inspection of granted code folders: structure, search, files, outlines."""

    @property
    def name(self) -> str:
        return "codeInspect"

    @property
    def description(self) -> str:
        return (
            "Inspect the user's own source code and projects (read-only, only folders the user granted): "
            "project overview with languages and manifests, folder tree, text search across the codebase, "
            "read a file by line range, or outline the classes and functions of a file. "
            "Use for questions about the user's code, repositories, architecture or where something is implemented."
        )

    @property
    def inputSchema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": list(OPERATIONS),
                    "description": "overview | tree | search | read | outline | roots (list granted folders)",
                },
                "path": {"type": "string", "description": "Folder or file (absolute, or relative to the granted folder)"},
                "query": {"type": "string", "description": "Text to find (search)"},
                "glob": {"type": "string", "description": "Optional file pattern for search, e.g. *.py"},
                "startLine": {"type": "integer", "description": "First line to read (read)"},
                "endLine": {"type": "integer", "description": "Last line to read (read)"},
                "depth": {"type": "integer", "description": "Tree depth, 1-8 (tree)"},
            },
            "required": ["operation"],
        }

    def run(self, args: Optional[Dict[str, Any]], context: ToolContext) -> ToolExecutionResult:
        args = args if isinstance(args, dict) else {}
        operation = str(args.get("operation") or "overview").strip().lower()
        if operation not in OPERATIONS:
            return ToolExecutionResult(success=False, reply_text=f"Unknown operation '{operation}'. Use one of: {', '.join(OPERATIONS)}.")

        policy = AccessPolicy.from_config(getattr(context.cfg, "code_access_roots", None))
        if not policy.granted:
            debug_log("codeInspect refused: no granted folders", "tools")
            return ToolExecutionResult(success=False, reply_text=_NOT_GRANTED)
        if operation == "roots":
            return ToolExecutionResult(success=True, reply_text=scanner.list_roots(policy))

        try:
            target = policy.resolve(args.get("path"))
        except AccessDenied as e:
            debug_log(f"codeInspect denied: {e}", "tools")
            return ToolExecutionResult(
                success=False,
                reply_text=f"Access denied ({e}). Readable folders:\n" + "\n".join(f"- {r}" for r in policy.roots),
            )

        handlers: dict[str, Callable[[], str]] = {
            "overview": lambda: scanner.overview(policy, target),
            "tree": lambda: scanner.tree(policy, target, int(args.get("depth") or 3)),
            "search": lambda: scanner.search(policy, target, str(args.get("query") or ""), args.get("glob") or None),
            "read": lambda: scanner.read(policy, target, int(args.get("startLine") or 1), args.get("endLine")),
            "outline": lambda: scanner.outline(policy, target),
        }
        context.user_print(f"🗂️ Inspecting code ({operation}): {target.name}")
        try:
            text = handlers[operation]()
        except AccessDenied as e:
            return ToolExecutionResult(success=False, reply_text=f"Access denied ({e}).")
        except (OSError, ValueError) as e:
            debug_log(f"codeInspect {operation} failed: {e}", "tools")
            return ToolExecutionResult(success=False, reply_text=f"Could not {operation} {target.name}: {e}")
        debug_log(f"codeInspect {operation} ok: {target} ({len(text)} chars)", "tools")
        return ToolExecutionResult(success=True, reply_text=text)
