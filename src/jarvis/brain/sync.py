"""Keep the Obsidian vault in step with Jarvis: skill map, hub, memory mirror.

Ownership (see brain.spec.md): only files with ``jarvis_id`` frontmatter are
ever rewritten or deleted. Skill-area notes are seeded once and then belong
to the user.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from typing import Optional

from ..debug import debug_log
from ..memory.graph import FIXED_BRANCH_IDS, GraphMemoryStore
from .vault import ObsidianVault

JARVIS_DIR = "JARVIS"
SKILLS_DIR = f"{JARVIS_DIR}/Habilidades"
MEMORY_DIR = f"{JARVIS_DIR}/Memória"

# Every structural label shown in the vault and the brain view (pt-BR, by
# user request). Keep them here rather than as scattered literals.
LABELS: dict[str, str] = {
    "hub": "Cérebro",
    "user": "Sobre mim",
    "directives": "Diretrizes",
    "world": "Mundo",
    "know": "O que eu sei",
    "part_of": "Parte de",
    "skills": "Habilidades",
    "memory": "Memória aprendida",
    "notes": "Notas",
    "hub_intro": "Mapa central do conhecimento. Gerado pelo J.A.R.V.I.S.",
    "skill_intro": "Área de habilidade. Edite à vontade: esta nota agora é sua.",
}

HUB_PATH = f"{JARVIS_DIR}/{LABELS['hub']}.md"

# Items longer than this stay plain text instead of becoming concept nodes.
_MAX_LINK_CHARS = 40
_UNSAFE_FILENAME = re.compile(r'[\\/:*?"<>|#^\[\]]')
_SUBSECTION = re.compile(r"^###\s+(?:[\d.]+\s+)?(.+?)\s*$")
_SECTION = re.compile(r"^##\s")
_BULLET = re.compile(r"^\s*[-*+]\s+(.*)$")


@dataclass
class SyncResult:
    written: int = 0
    removed: int = 0


def _safe_title(name: str) -> str:
    title = " ".join(_UNSAFE_FILENAME.sub(" ", name or "").split()).strip(" .")
    return title or "Sem título"


def _frontmatter(fields: dict) -> str:
    lines = ["---"]
    for key, value in fields.items():
        if isinstance(value, list):
            lines.append(f"{key}: [{', '.join(value)}]")
        else:
            lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines)


# ── Skill map ──────────────────────────────────────────────────────────────


def parse_skill_areas(prompt_text: str) -> list[tuple[str, list[str]]]:
    """Each ``###`` subsection of the prompt with its first bullet list."""
    areas: list[tuple[str, list[str]]] = []
    current: Optional[tuple[str, list[str]]] = None
    list_closed = False
    for line in prompt_text.splitlines():
        sub = _SUBSECTION.match(line)
        if sub:
            current = (sub.group(1).strip(), [])
            areas.append(current)
            list_closed = False
            continue
        if _SECTION.match(line):
            current = None
            continue
        if current is None or list_closed:
            continue
        bullet = _BULLET.match(line)
        if bullet:
            item = bullet.group(1).strip().rstrip(";.,").strip()
            if item:
                current[1].append(item)
        elif line.strip() and current[1]:
            list_closed = True  # first list ended; later lists are guidance, not skills
    return [(name, items) for name, items in areas if items]


def _skill_note(items: list[str]) -> str:
    body = [
        _frontmatter({"jarvis_kind": "area", "tags": ["jarvis/habilidade"]}),
        f"> {LABELS['part_of']} [[{LABELS['hub']}]]",
        f"> {LABELS['skill_intro']}",
        "",
        f"## {LABELS['know']}",
        "",
    ]
    for item in items:
        if len(item) <= _MAX_LINK_CHARS and not _UNSAFE_FILENAME.search(item):
            body.append(f"- [[{item}]]")
        else:
            body.append(f"- {item}")
    return "\n".join(body) + "\n"


def seed_skill_map(vault: ObsidianVault, prompt_text: str) -> int:
    """Create missing skill-area notes; never touches existing ones."""
    created = 0
    for name, items in parse_skill_areas(prompt_text):
        rel = f"{SKILLS_DIR}/{_safe_title(name)}.md"
        if vault.get(rel) is not None:
            continue
        vault.write_note(rel, _skill_note(items))
        created += 1
    if created:
        debug_log(f"brain: seeded {created} skill area(s)", "brain")
    return created


# ── Memory mirror ──────────────────────────────────────────────────────────


def _memory_titles(store: GraphMemoryStore) -> dict[str, str]:
    nodes = sorted(store.get_all_nodes(), key=lambda n: (n.created_at or "", n.id))
    titles: dict[str, str] = {"root": LABELS["hub"]}
    used: set[str] = {LABELS["hub"].casefold()}
    for node in nodes:
        if node.id == "root":
            continue
        base = LABELS[node.id] if node.id in FIXED_BRANCH_IDS else _safe_title(node.name)
        title = base
        if title.casefold() in used:
            title = f"{base} ({node.id[:6]})"
        used.add(title.casefold())
        titles[node.id] = title
    return titles


def _branch_of(store: GraphMemoryStore, node_id: str) -> Optional[str]:
    for ancestor in store.get_ancestors(node_id):
        if ancestor.id in FIXED_BRANCH_IDS:
            return ancestor.id
    return node_id if node_id in FIXED_BRANCH_IDS else None


def sync_memory_to_vault(store: GraphMemoryStore, vault: ObsidianVault) -> SyncResult:
    result = SyncResult()
    titles = _memory_titles(store)
    expected: set[str] = set()
    for node in store.get_all_nodes():
        if node.id == "root":
            continue
        title = titles[node.id]
        rel = f"{MEMORY_DIR}/{title}.md"
        expected.add(rel)
        branch = _branch_of(store, node.id)
        parent_title = titles.get(node.parent_id or "root", LABELS["hub"])
        lines = [
            _frontmatter({
                "jarvis_id": node.id,
                "jarvis_kind": "memory",
                "area": LABELS.get(branch or "", LABELS["memory"]),
                "tags": ["jarvis/memoria"],
            }),
            f"> {LABELS['part_of']} [[{parent_title}]]",
        ]
        if node.description:
            lines += [f"> {node.description.strip()}"]
        facts = [ln.strip().lstrip("-*• ").strip() for ln in (node.data or "").splitlines()]
        facts = [f for f in facts if f]
        if facts:
            lines += ["", f"## {LABELS['know']}", ""] + [f"- {f}" for f in facts]
        if vault.write_note(rel, "\n".join(lines) + "\n"):
            result.written += 1

    for note in vault.notes():
        if note.path.startswith(MEMORY_DIR + "/") and note.jarvis_owned and note.path not in expected:
            if vault.delete_note(note.path):
                result.removed += 1
    return result


def write_hub(vault: ObsidianVault) -> bool:
    skills = sorted(n.title for n in vault.notes() if n.folder == SKILLS_DIR)
    lines = [
        _frontmatter({"jarvis_id": "cerebro", "jarvis_kind": "hub", "tags": ["jarvis"]}),
        f"> {LABELS['hub_intro']}",
        "",
        f"## {LABELS['skills']}",
        "",
        *[f"- [[{s}]]" for s in skills],
        "",
        f"## {LABELS['memory']}",
        "",
        *[f"- [[{LABELS[b]}]]" for b in ("user", "directives", "world")],
    ]
    return vault.write_note(HUB_PATH, "\n".join(lines) + "\n")


def sync_brain(vault: ObsidianVault, store: Optional[GraphMemoryStore], prompt_text: str) -> SyncResult:
    """Idempotent full sync: seed skills, mirror memory, refresh the hub."""
    seed_skill_map(vault, prompt_text)
    result = sync_memory_to_vault(store, vault) if store is not None else SyncResult()
    if write_hub(vault):
        result.written += 1
    debug_log(f"brain sync: {result.written} written, {result.removed} removed", "brain")
    return result


class BrainSyncer:
    """Daemon helper: initial sync in the background + debounced re-sync on graph writes."""

    DEBOUNCE_SEC = 2.0

    def __init__(self, vault_path: str, db_path: str, prompt_text: str) -> None:
        self.vault = ObsidianVault(vault_path)
        self._db_path = db_path
        self._prompt_text = prompt_text
        self._timer: Optional[threading.Timer] = None
        self._lock = threading.Lock()

    def sync_now(self) -> SyncResult:
        with self._lock:
            store = GraphMemoryStore(self._db_path)
            try:
                return sync_brain(self.vault, store, self._prompt_text)
            finally:
                store.close()

    def _safe_sync(self) -> None:
        try:
            self.sync_now()
        except Exception as e:
            debug_log(f"brain sync failed: {e}", "brain")

    def start(self) -> None:
        threading.Thread(target=self._safe_sync, daemon=True, name="brain-sync").start()

    def on_graph_mutation(self, action: str = "", node_id: str = "", branch: Optional[str] = None, **_: object) -> None:
        if self._timer is not None:
            self._timer.cancel()
        self._timer = threading.Timer(self.DEBOUNCE_SEC, self._safe_sync)
        self._timer.daemon = True
        self._timer.start()
