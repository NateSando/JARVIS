"""Read/write access to an Obsidian vault (plain Markdown files on disk).

Language-agnostic: parsing keys off Markdown/Obsidian syntax only (wikilinks,
bullets, frontmatter, ``#tags``), never off words in any particular language.
See brain.spec.md.
"""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..debug import debug_log

MAX_POINTS = 60

_WIKILINK = re.compile(r"\[\[([^\]\|#]*)(?:#[^\]\|]*)?(?:\|([^\]]*))?\]\]")
_MD_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_INLINE_TAG = re.compile(r"(?:(?<=\s)|^)#([^\W\d][\w/-]*)", re.UNICODE)
_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s+)?(.*)$")
_HEADING = re.compile(r"^\s*#{1,6}\s")
_EMPHASIS = re.compile(r"(\*\*|__|~~|==|`|\*)")


def fold(text: str) -> str:
    """Case- and accent-insensitive form for matching (any script)."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


@dataclass
class Note:
    path: str  # vault-relative, POSIX separators
    title: str
    folder: str
    frontmatter: dict = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    points: list[str] = field(default_factory=list)

    @property
    def jarvis_owned(self) -> bool:
        return "jarvis_id" in self.frontmatter


@dataclass
class SearchHit:
    note: Note
    score: int


def _parse_frontmatter(lines: list[str]) -> tuple[dict, list[str]]:
    if not lines or lines[0].strip() != "---":
        return {}, lines
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        return {}, lines
    data: dict = {}
    key: Optional[str] = None
    for raw in lines[1:end]:
        if raw.strip().startswith("- ") and key:
            data.setdefault(key, [])
            if isinstance(data[key], list):
                data[key].append(raw.strip()[2:].strip().strip("'\""))
            continue
        if ":" not in raw:
            continue
        key, _, value = raw.partition(":")
        key = key.strip()
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            data[key] = [v.strip().strip("'\"") for v in value[1:-1].split(",") if v.strip()]
        elif value:
            data[key] = value.strip("'\"")
        else:
            data[key] = []
    return data, lines[end + 1:]


def _clean_point(text: str) -> str:
    text = _WIKILINK.sub(lambda m: (m.group(2) or m.group(1)).strip(), text)
    text = _MD_LINK.sub(lambda m: m.group(1), text)
    text = _EMPHASIS.sub("", text)
    return " ".join(text.split())


def parse_note(text: str, rel_path: str) -> Note:
    rel = Path(rel_path)
    lines = text.splitlines()
    frontmatter, body = _parse_frontmatter(lines)

    links: list[str] = []
    for m in _WIKILINK.finditer("\n".join(body)):
        target = _nfc(m.group(1).strip())
        if target and target not in links:
            links.append(target)

    tags: list[str] = []
    fm_tags = frontmatter.get("tags", [])
    for t in ([fm_tags] if isinstance(fm_tags, str) else fm_tags):
        t = str(t).lstrip("#").strip()
        if t and t not in tags:
            tags.append(t)

    bullets: list[str] = []
    paragraphs: list[str] = []
    in_code = False
    for line in body:
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        if not _HEADING.match(line):
            for t in _INLINE_TAG.findall(line):
                if t not in tags:
                    tags.append(t)
        m = _BULLET.match(line)
        if m:
            point = _clean_point(m.group(1))
            if point:
                bullets.append(point)
        elif line.strip() and not _HEADING.match(line) and not line.lstrip().startswith(">"):
            point = _clean_point(line)
            if point:
                paragraphs.append(point)

    return Note(
        path=rel.as_posix(),
        title=_nfc(rel.stem),
        folder=_nfc(rel.parent.as_posix()) if rel.parent.as_posix() != "." else "",
        frontmatter=frontmatter,
        tags=tags,
        links=links,
        points=(bullets or paragraphs)[:MAX_POINTS],
    )


class ObsidianVault:
    """A vault folder. Parsing is cached per file by (mtime, size)."""

    def __init__(self, root: str | os.PathLike) -> None:
        self.root = Path(root).expanduser()
        self._cache: dict[str, tuple[tuple[int, int], Note]] = {}

    def exists(self) -> bool:
        return self.root.is_dir()

    def _resolve(self, rel_path: str) -> Path:
        root = self.root.resolve()
        target = (root / rel_path).resolve()
        if target != root and root not in target.parents:
            raise ValueError(f"path escapes the vault: {rel_path}")
        return target

    def _load(self, path: Path) -> Optional[Note]:
        rel = path.relative_to(self.root).as_posix()
        try:
            st = path.stat()
            sig = (st.st_mtime_ns, st.st_size)
            cached = self._cache.get(rel)
            if cached and cached[0] == sig:
                return cached[1]
            note = parse_note(path.read_text(encoding="utf-8", errors="replace"), rel)
            self._cache[rel] = (sig, note)
            return note
        except OSError as e:
            debug_log(f"vault read failed for {rel}: {e}", "brain")
            return None

    def notes(self) -> list[Note]:
        if not self.exists():
            return []
        found: list[Note] = []
        seen: set[str] = set()
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            for name in sorted(filenames):
                if not name.lower().endswith(".md") or name.startswith("."):
                    continue
                note = self._load(Path(dirpath) / name)
                if note is not None:
                    found.append(note)
                    seen.add(note.path)
        for stale in set(self._cache) - seen:
            self._cache.pop(stale, None)
        return found

    def get(self, rel_path: str) -> Optional[Note]:
        path = self._resolve(rel_path)
        return self._load(path) if path.is_file() else None

    def write_note(self, rel_path: str, content: str) -> bool:
        """Write a note; returns False when the file already had this content."""
        path = self._resolve(rel_path)
        if path.is_file() and path.read_text(encoding="utf-8", errors="replace") == content:
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(path)
        return True

    def delete_note(self, rel_path: str) -> bool:
        path = self._resolve(rel_path)
        if not path.is_file():
            return False
        path.unlink()
        self._cache.pop(Path(rel_path).as_posix(), None)
        return True

    def search(self, words: list[str], limit: int = 5, exclude_prefix: Optional[str] = None,
               include_owned: bool = True) -> list[SearchHit]:
        terms = [fold(w) for w in words if w and w.strip()]
        if not terms:
            return []
        hits: list[SearchHit] = []
        for note in self.notes():
            if exclude_prefix and note.path.startswith(exclude_prefix):
                continue
            if not include_owned and note.jarvis_owned:
                continue
            title = fold(note.title)
            points = [fold(p) for p in note.points]
            score = 0
            for term in terms:
                if term in title:
                    score += 3
                score += sum(1 for p in points if term in p)
            if score:
                hits.append(SearchHit(note=note, score=score))
        hits.sort(key=lambda h: (-h.score, h.note.title))
        return hits[:limit]
