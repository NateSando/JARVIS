"""Obsidian brain: vault access, sync with the knowledge graph, brain map, recall.

See brain.spec.md.
"""

from __future__ import annotations

from ..debug import debug_log
from .sync import MEMORY_DIR
from .vault import ObsidianVault

_vaults: dict[str, ObsidianVault] = {}


def get_vault(path: str) -> ObsidianVault:
    """Shared vault instance per path so the parse cache survives across replies."""
    vault = _vaults.get(path)
    if vault is None:
        vault = _vaults[path] = ObsidianVault(path)
    return vault


def vault_enrichment_parts(vault_path: str, words: list[str], limit: int = 3) -> list[str]:
    """User notes matching ``words`` as enrichment lines. Fail-open: errors give []."""
    try:
        vault = get_vault(vault_path)
        if not vault.exists():
            return []
        hits = vault.search(words, limit=limit, exclude_prefix=MEMORY_DIR + "/", include_owned=False)
        parts = []
        for hit in hits:
            preview = "; ".join(hit.note.points)[:300]
            if preview:
                parts.append(f"[Obsidian › {hit.note.title}] {preview}")
        return parts
    except Exception as e:
        debug_log(f"vault enrichment failed: {e}", "brain")
        return []
