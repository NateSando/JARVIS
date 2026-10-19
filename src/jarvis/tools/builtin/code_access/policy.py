"""Access policy for the read-only code and system tools.

Permission model (see code_access.spec.md):
- Code: only folders the user listed in ``code_access_roots`` are readable.
  Every path is resolved (symlinks followed) and must stay inside a root.
- Secrets: credential-shaped files are never opened, whatever the root says.
- Noise: vendor, VCS and build folders are skipped when walking.
"""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Optional, Sequence

MAX_FILE_BYTES = 512 * 1024
_BINARY_SNIFF_BYTES = 8192

# Credential-shaped files. Matched case-insensitively against the file name.
SECRET_FILE_PATTERNS: tuple[str, ...] = (
    ".env", ".env.*", "*.env",
    "*.pem", "*.key", "*.p12", "*.pfx", "*.crt", "*.cer", "*.der",
    "*.keystore", "*.jks", "*.kdbx", "*.gpg", "*.asc",
    "id_rsa*", "id_dsa*", "id_ecdsa*", "id_ed25519*", "known_hosts", "authorized_keys",
    ".npmrc", ".pypirc", ".netrc", ".git-credentials", ".htpasswd",
    "credentials", "credentials.*", "secrets.*", "*secret*.json", "*.tfstate", "*.tfstate.*",
    "service-account*.json",
)
# Documented templates are safe and useful for understanding a project.
SAFE_TEMPLATE_PATTERNS: tuple[str, ...] = (".env.example", ".env.sample", ".env.template", ".env.dist")

IGNORED_DIRS: frozenset[str] = frozenset({
    ".git", ".hg", ".svn", "node_modules", "bower_components", ".venv", "venv", "env",
    ".mamba_env", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox",
    "dist", "build", "out", "target", ".next", ".nuxt", ".svelte-kit", ".angular",
    ".gradle", ".idea", "coverage", ".cache", "vendor", "Pods", "DerivedData",
})
# Dot-folders are skipped except those that describe how the project is built.
ALLOWED_DOT_DIRS: frozenset[str] = frozenset({".github", ".gitlab", ".circleci", ".devcontainer", ".claude"})


class AccessDenied(Exception):
    """Raised when a path is outside the granted folders or is a secret."""


def is_secret_file(path: Path) -> bool:
    name = path.name.casefold()
    if any(fnmatch.fnmatch(name, p) for p in SAFE_TEMPLATE_PATTERNS):
        return False
    return any(fnmatch.fnmatch(name, p) for p in SECRET_FILE_PATTERNS)


def is_ignored_dir(name: str) -> bool:
    if name in IGNORED_DIRS:
        return True
    return name.startswith(".") and name not in ALLOWED_DOT_DIRS


def is_binary(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            return b"\x00" in fh.read(_BINARY_SNIFF_BYTES)
    except OSError:
        return True


@dataclass(frozen=True)
class AccessPolicy:
    roots: tuple[Path, ...]

    @classmethod
    def from_config(cls, raw_roots: Optional[Iterable[str]]) -> "AccessPolicy":
        roots: list[Path] = []
        for raw in raw_roots or ():
            if not isinstance(raw, str) or not raw.strip():
                continue
            path = Path(os.path.expanduser(raw.strip())).resolve()
            if path.is_dir() and path not in roots:
                roots.append(path)
        return cls(roots=tuple(roots))

    @property
    def granted(self) -> bool:
        return bool(self.roots)

    def contains(self, path: Path) -> Optional[Path]:
        for root in self.roots:
            if path == root or path.is_relative_to(root):
                return root
        return None

    def resolve(self, raw: Optional[str]) -> Path:
        """Resolve a user/LLM-supplied path to a real path inside a granted root."""
        if not self.granted:
            raise AccessDenied("no folders granted")
        if not raw or not str(raw).strip():
            if len(self.roots) == 1:
                return self.roots[0]
            raise AccessDenied("path required when several folders are granted")
        candidate = Path(os.path.expanduser(str(raw).strip()))
        if not candidate.is_absolute():
            matches = [(root / candidate).resolve() for root in self.roots]
            existing = [m for m in matches if m.exists()]
            candidate = (existing or matches)[0]
        resolved = candidate.resolve()
        if self.contains(resolved) is None:
            raise AccessDenied(f"outside the granted folders: {raw}")
        if resolved.is_file() and is_secret_file(resolved):
            raise AccessDenied(f"secret file is never read: {resolved.name}")
        return resolved

    def root_of(self, path: Path) -> Path:
        root = self.contains(path)
        if root is None:
            raise AccessDenied(f"outside the granted folders: {path}")
        return root

    def walk_files(self, start: Path, limit: int) -> Iterator[Path]:
        """Readable, non-secret files under ``start`` (inside the roots), up to ``limit``."""
        count = 0
        for dirpath, dirnames, filenames in os.walk(start):
            dirnames[:] = sorted(d for d in dirnames if not is_ignored_dir(d))
            for name in sorted(filenames):
                path = Path(dirpath) / name
                if is_secret_file(path):
                    continue
                try:
                    real = path.resolve()
                except OSError:
                    continue
                if self.contains(real) is None:
                    continue  # symlink pointing outside the granted folders
                yield path
                count += 1
                if count >= limit:
                    return


def relative_label(path: Path, roots: Sequence[Path]) -> str:
    for root in roots:
        if path == root:
            return root.name + "/"
        if path.is_relative_to(root):
            return path.relative_to(root).as_posix()
    return path.name
