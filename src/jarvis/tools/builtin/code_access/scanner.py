"""Read-only structure queries over a granted code folder.

Every function receives an ``AccessPolicy`` and a path already resolved by
it, returns plain text (raw data for the LLM), and caps its own output so a
huge repository can't blow the context window.
"""

from __future__ import annotations

import ast
import re
from collections import Counter
from pathlib import Path
from typing import Optional

from ....utils.redact import scrub_secrets
from .policy import MAX_FILE_BYTES, AccessDenied, AccessPolicy, is_binary, is_ignored_dir, is_secret_file, relative_label

MAX_WALK_FILES = 20000
MAX_TREE_ENTRIES = 400
MAX_SEARCH_MATCHES = 60
MAX_READ_LINES = 400
MAX_LINE_CHARS = 240
MAX_OUTLINE_SYMBOLS = 300

LANGUAGES: dict[str, str] = {
    ".py": "Python", ".pyi": "Python", ".ipynb": "Jupyter",
    ".ts": "TypeScript", ".tsx": "TypeScript", ".js": "JavaScript", ".jsx": "JavaScript",
    ".mjs": "JavaScript", ".cjs": "JavaScript", ".vue": "Vue", ".svelte": "Svelte",
    ".html": "HTML", ".css": "CSS", ".scss": "SCSS", ".sass": "SCSS", ".less": "Less",
    ".java": "Java", ".kt": "Kotlin", ".kts": "Kotlin", ".scala": "Scala", ".groovy": "Groovy",
    ".go": "Go", ".rs": "Rust", ".c": "C", ".h": "C", ".cpp": "C++", ".cc": "C++", ".hpp": "C++",
    ".cs": "C#", ".swift": "Swift", ".m": "Objective-C", ".rb": "Ruby", ".php": "PHP",
    ".dart": "Dart", ".lua": "Lua", ".r": "R", ".jl": "Julia", ".ex": "Elixir", ".exs": "Elixir",
    ".sh": "Shell", ".bash": "Shell", ".zsh": "Shell", ".ps1": "PowerShell", ".bat": "Batch",
    ".sql": "SQL", ".prisma": "Prisma", ".graphql": "GraphQL", ".proto": "Protobuf",
    ".yml": "YAML", ".yaml": "YAML", ".toml": "TOML", ".json": "JSON", ".xml": "XML",
    ".md": "Markdown", ".rst": "reStructuredText", ".tf": "Terraform", ".dockerfile": "Docker",
}

MANIFESTS: tuple[str, ...] = (
    "package.json", "pnpm-workspace.yaml", "tsconfig.json", "angular.json", "nest-cli.json",
    "vite.config.ts", "next.config.js", "pyproject.toml", "requirements.txt", "setup.py",
    "Pipfile", "environment.yml", "Cargo.toml", "go.mod", "pom.xml", "build.gradle",
    "build.gradle.kts", "Gemfile", "composer.json", "pubspec.yaml", "Package.swift",
    "Dockerfile", "docker-compose.yml", "docker-compose.yaml", "compose.yaml", "Makefile",
    "CMakeLists.txt", "schema.prisma", ".env.example", "README.md", "CLAUDE.md",
)

# Declaration keywords across common programming languages (not human language).
_DECLARATION = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:public\s+|private\s+|protected\s+|internal\s+)?"
    r"(?:static\s+)?(?:abstract\s+)?(?:async\s+)?"
    r"(class|interface|enum|function|def|fn|func|struct|trait|type|module|record)\s+"
    r"([A-Za-z_$][\w$]*)"
)
_ARROW = re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\(?[^=]*\)?\s*=>")


def _language(path: Path) -> Optional[str]:
    if path.name == "Dockerfile":
        return "Docker"
    return LANGUAGES.get(path.suffix.lower())


def _clip(line: str) -> str:
    line = line.rstrip("\n\r")
    return line if len(line) <= MAX_LINE_CHARS else line[:MAX_LINE_CHARS] + "…"


def _read_text(path: Path) -> str:
    if is_secret_file(path):
        raise AccessDenied(f"secret file is never read: {path.name}")
    if not path.is_file():
        raise FileNotFoundError(str(path))
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError(f"file too large to read ({path.stat().st_size // 1024} KB > {MAX_FILE_BYTES // 1024} KB)")
    if is_binary(path):
        raise ValueError("binary file")
    return path.read_text(encoding="utf-8", errors="replace")


def list_roots(policy: AccessPolicy) -> str:
    return "Granted code folders (read-only):\n" + "\n".join(f"- {r}" for r in policy.roots)


def overview(policy: AccessPolicy, start: Path) -> str:
    if not start.is_dir():
        raise NotADirectoryError(str(start))
    languages: Counter[str] = Counter()
    lines_by_lang: Counter[str] = Counter()
    total = 0
    manifests: list[str] = []
    for path in policy.walk_files(start, MAX_WALK_FILES):
        total += 1
        if path.name in MANIFESTS:
            manifests.append(relative_label(path, (start,)))
        lang = _language(path)
        if lang:
            languages[lang] += 1
            try:
                if path.stat().st_size <= MAX_FILE_BYTES:
                    with path.open("rb") as fh:
                        lines_by_lang[lang] += sum(1 for _ in fh)
            except OSError:
                pass

    top_level: list[str] = []
    for child in sorted(start.iterdir(), key=lambda p: (not p.is_dir(), p.name.casefold())):
        if child.is_dir():
            if not is_ignored_dir(child.name):
                top_level.append(child.name + "/")
        elif not is_secret_file(child):
            top_level.append(child.name)

    out = [f"Project: {start.name} ({start})", f"Files scanned: {total}" + (" (capped)" if total >= MAX_WALK_FILES else "")]
    if languages:
        out.append("Languages (files, lines):")
        out += [f"- {lang}: {count} files, ~{lines_by_lang[lang]} lines" for lang, count in languages.most_common(12)]
    if manifests:
        out.append("Manifests and key files:")
        out += [f"- {m}" for m in sorted(manifests)[:40]]
    out.append("Top level:")
    out += [f"- {name}" for name in top_level[:80]]
    return "\n".join(out)


def tree(policy: AccessPolicy, start: Path, depth: int = 3) -> str:
    if not start.is_dir():
        raise NotADirectoryError(str(start))
    depth = max(1, min(int(depth), 8))
    lines = [f"{start.name}/"]
    truncated = False

    def walk(folder: Path, level: int) -> None:
        nonlocal truncated
        try:
            children = sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.casefold()))
        except OSError:
            return
        for child in children:
            if len(lines) >= MAX_TREE_ENTRIES:
                truncated = True
                return
            if child.is_dir():
                if is_ignored_dir(child.name) or policy.contains(child.resolve()) is None:
                    continue
                lines.append("  " * level + child.name + "/")
                if level < depth:
                    walk(child, level + 1)
            elif not is_secret_file(child):
                lines.append("  " * level + child.name)

    walk(start, 1)
    if truncated:
        lines.append(f"… (truncated at {MAX_TREE_ENTRIES} entries; ask for a sub-folder)")
    return "\n".join(lines)


def search(policy: AccessPolicy, start: Path, query: str, glob: Optional[str] = None) -> str:
    if not query or not query.strip():
        raise ValueError("query is required for search")
    needle = query.strip().casefold()
    base = start if start.is_dir() else start.parent
    files = [start] if start.is_file() else policy.walk_files(base, MAX_WALK_FILES)
    hits: list[str] = []
    for path in files:
        if glob and not path.match(glob):
            continue
        if path.stat().st_size > MAX_FILE_BYTES or is_binary(path):
            continue
        try:
            with path.open("r", encoding="utf-8", errors="replace") as fh:
                for number, line in enumerate(fh, 1):
                    if needle in line.casefold():
                        label = relative_label(path, (base,))
                        hits.append(f"{label}:{number}: {scrub_secrets(_clip(line).strip())}")
                        if len(hits) >= MAX_SEARCH_MATCHES:
                            hits.append(f"… (stopped at {MAX_SEARCH_MATCHES} matches; narrow the query or path)")
                            return "\n".join(hits)
        except OSError:
            continue
    return "\n".join(hits) if hits else f"No matches for '{query.strip()}'."


def read(policy: AccessPolicy, path: Path, start_line: int = 1, end_line: Optional[int] = None) -> str:
    text = _read_text(path)
    lines = text.splitlines()
    start = max(1, int(start_line or 1))
    end = min(len(lines), int(end_line) if end_line else start + MAX_READ_LINES - 1)
    end = min(end, start + MAX_READ_LINES - 1)
    width = len(str(end))
    body = [f"{str(n).rjust(width)} | {_clip(lines[n - 1])}" for n in range(start, end + 1)]
    header = f"{relative_label(path, policy.roots)} (lines {start}-{end} of {len(lines)})"
    return header + "\n" + scrub_secrets("\n".join(body))


def outline(policy: AccessPolicy, path: Path) -> str:
    text = _read_text(path)
    symbols: list[str] = []
    if path.suffix.lower() in (".py", ".pyi"):
        try:
            tree_ = ast.parse(text)
        except SyntaxError as e:
            raise ValueError(f"cannot parse Python file: {e.msg} (line {e.lineno})") from e

        def visit(node: ast.AST, indent: int) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.ClassDef):
                    symbols.append(f"{'  ' * indent}class {child.name}  L{child.lineno}")
                    visit(child, indent + 1)
                elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    prefix = "async def" if isinstance(child, ast.AsyncFunctionDef) else "def"
                    symbols.append(f"{'  ' * indent}{prefix} {child.name}()  L{child.lineno}")

        visit(tree_, 0)
    else:
        for number, line in enumerate(text.splitlines(), 1):
            m = _DECLARATION.match(line)
            if m:
                indent = (len(line) - len(line.lstrip())) // 2
                symbols.append(f"{'  ' * min(indent, 4)}{m.group(1)} {m.group(2)}  L{number}")
                continue
            m = _ARROW.match(line)
            if m:
                symbols.append(f"function {m.group(1)}  L{number}")
    label = relative_label(path, policy.roots)
    if not symbols:
        return f"{label}: no declarations found."
    more = len(symbols) > MAX_OUTLINE_SYMBOLS
    return f"{label} outline:\n" + "\n".join(symbols[:MAX_OUTLINE_SYMBOLS]) + ("\n…" if more else "")
