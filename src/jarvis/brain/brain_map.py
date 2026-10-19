"""Turn a vault into the node/link/area structure the brain view renders."""

from __future__ import annotations

from typing import Optional

from .sync import LABELS
from .vault import Note, ObsidianVault


def _key(title: str) -> str:
    return title.casefold()


def _kind(note: Note) -> str:
    kind = str(note.frontmatter.get("jarvis_kind", "")).strip()
    return kind if kind in ("hub", "area", "memory") else "note"


def build_brain_map(vault: ObsidianVault) -> dict:
    notes = vault.notes()
    by_key: dict[str, Note] = {}
    for note in notes:
        by_key.setdefault(_key(note.title), note)

    nodes: dict[str, dict] = {}
    for key, note in by_key.items():
        kind = _kind(note)
        area: Optional[str] = str(note.frontmatter.get("area") or "").strip() or None
        if area is None and kind in ("area", "hub"):
            area = note.title
        nodes[key] = {
            "id": key,
            "label": note.title,
            "kind": kind,
            "area": area,
            "folder": note.folder,
            "path": note.path,
            "points": list(note.points),
            "tags": list(note.tags),
            "backlinks": [],
            "degree": 0,
        }

    pairs: set[tuple[str, str]] = set()
    for note in sorted(by_key.values(), key=lambda n: n.path):
        source = _key(note.title)
        for target_title in note.links:
            target = _key(target_title)
            if target == source:
                continue
            if target not in nodes:
                nodes[target] = {
                    "id": target,
                    "label": target_title,
                    "kind": "concept",
                    "area": None,
                    "folder": "",
                    "path": None,
                    "points": [],
                    "tags": [],
                    "backlinks": [],
                    "degree": 0,
                }
            if note.title not in nodes[target]["backlinks"]:
                nodes[target]["backlinks"].append(note.title)
            pairs.add(tuple(sorted((source, target))))

    for a, b in pairs:
        nodes[a]["degree"] += 1
        nodes[b]["degree"] += 1

    # Areas: explicit first, then the note's top-level folder, then inherit
    # from the first linker that has one (preferring skill areas over the
    # hub), then a default.
    def linker_area(node: dict) -> Optional[str]:
        candidates = [nodes[_key(t)] for t in node["backlinks"] if _key(t) in nodes]
        candidates.sort(key=lambda n: (n["kind"] == "hub", n["kind"] != "area"))
        for c in candidates:
            if c["area"]:
                return c["area"]
        return None

    for node in nodes.values():
        if node["area"] is None and node["folder"]:
            node["area"] = node["folder"].split("/")[0]
    for _ in range(3):  # a few passes let areas flow down short chains
        for node in nodes.values():
            if node["area"] is None:
                node["area"] = linker_area(node)
    for node in nodes.values():
        if node["area"] is None:
            node["area"] = LABELS["notes"]

    counts: dict[str, int] = {}
    for node in nodes.values():
        counts[node["area"]] = counts.get(node["area"], 0) + 1
    areas = [{"name": name, "count": count} for name, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]

    return {
        "nodes": sorted(nodes.values(), key=lambda n: n["id"]),
        "links": [{"source": a, "target": b} for a, b in sorted(pairs)],
        "areas": areas,
    }
