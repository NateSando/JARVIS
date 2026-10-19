# Code & System Access Specification

## Overview

Two built-in tools let Jarvis look at the user's own code and computer, strictly read-only and only after the user grants it:

- `codeInspect`: structure and contents of project folders the user listed.
- `systemInfo`: operating system, hardware, process and disk snapshot.

Both return raw data (per CLAUDE.md, tools never post-process with an LLM); the reply loop phrases the answer.

## Permission Model

| Setting | Default | Meaning |
|---------|---------|---------|
| `code_access_roots` | `[]` | Folders `codeInspect` may read. Empty = no code access at all. |
| `system_access_enabled` | `false` | Whether `systemInfo` may read the machine. |

Granting is an explicit user action in Settings → "🔐 Code & System Access" (or `config.json`). Until then the tools stay registered (so the router can pick them and Jarvis can tell the user how to enable them) but refuse with a message naming the setting, and reveal nothing.

There is no write, delete, execute or shell capability. Mutations and command execution are out of scope by design (zero-trust: an LLM never runs arbitrary actions on the user's machine).

## codeInspect

Operations (`operation` is required; default when absent is `overview`):

| Operation | Input | Output |
|-----------|-------|--------|
| `roots` | none | Granted folders |
| `overview` | `path` (folder) | Files scanned, languages with file/line counts, manifests and key files (package.json, pyproject.toml, Dockerfile, README…), top-level entries |
| `tree` | `path`, `depth` 1-8 (default 3) | Indented folder tree, capped at 400 entries |
| `search` | `path`, `query`, optional `glob` | `relative/path:line: text` matches, case-insensitive literal, capped at 60 |
| `read` | `path` (file), `startLine`, `endLine` | Numbered lines, at most 400 per call |
| `outline` | `path` (file) | Classes/functions with line numbers (Python via `ast`; other languages via declaration keywords) |

Path resolution (`policy.AccessPolicy.resolve`):
- `~` expanded; relative paths resolve against the granted roots (the first root where the path exists).
- The path is fully resolved (symlinks followed, `..` collapsed) and must be inside a granted root, otherwise access is denied. This blocks traversal and symlink escapes.
- With a single root, an empty `path` means that root.

Safety rules:
- **Secret files are never opened or listed**, even inside a granted root: `.env*` (except `.env.example/.sample/.template/.dist`), keys and certificates (`*.pem`, `*.key`, `id_rsa*`, …), credential stores (`.npmrc`, `.netrc`, `.git-credentials`, `credentials*`, `*.tfstate`, …). See `SECRET_FILE_PATTERNS`.
- **Content is scrubbed** with `utils.redact.scrub_secrets` (tokens, keys, passwords, emails, JWTs) before it leaves the tool, preserving line layout.
- Vendor, VCS, build and cache folders (`node_modules`, `.git`, `.venv`, `dist`, …) and other dot-folders (except CI/devcontainer ones) are skipped. Binary files (NUL byte sniff) and files over 512 KB are not read.
- Every operation caps its output (files walked, entries, matches, lines, line width) so a large repository cannot flood the context window.

## systemInfo

Sections (`section`, default `overview`):
- `overview`: OS/release, architecture, CPU cores/threads/frequency/usage, RAM and swap, uptime, Python version, load average.
- `processes`: process count and the top 10 by CPU and by memory (PID, CPU %, RSS MB, name).
- `disks`: per-mount usage.

Privacy: process **names only** (never command lines, which routinely carry tokens), no environment variables, no user names, no network addresses or hostnames. Uses `psutil`; missing `psutil` yields an honest failure.

## Logging

`debug_log(..., "tools")` on refusals, denials, failures and successful operations (path + output size, never content). The CLI line uses `🗂️` / `🖥️`.
