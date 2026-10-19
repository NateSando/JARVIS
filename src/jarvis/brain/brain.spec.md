# Obsidian Brain Specification

## Overview

The brain is the user-facing picture of everything Jarvis knows about the user's knowledge: an Obsidian vault rendered as a living, force-directed graph ("cérebro"). It has three sources, all of which end up as plain Markdown notes in one vault:

1. **Skill map**: one note per skill area, seeded from the master system prompt (`system_prompt.py`). Every `###` subsection's first bullet list becomes the area's "pontos que eu sei"; short items are wikilinks, so each becomes a concept node like in Obsidian's graph view.
2. **Learned memory**: the knowledge graph (`memory/graph.py`) mirrored one node = one note, with a wikilink to its parent, so the tree shows up in Obsidian too.
3. **The user's own notes**: anything else in the vault. Pointing `obsidian_vault_path` at an existing vault makes the user's notes part of the brain and of Jarvis's recall.

The vault is the single on-disk representation; the brain view and recall both read the vault, never a private copy. Everything is local files: no network, no cloud sync, no telemetry (privacy first).

## Vault Layout

```
<vault>/
  JARVIS/
    Cérebro.md               hub note (Jarvis-owned, regenerated)
    Habilidades/<Área>.md    skill areas (seeded once, then user-owned)
    Memória/<Nó>.md          knowledge graph mirror (Jarvis-owned, regenerated)
  ...                        the user's own notes (never written by Jarvis)
```

Ownership rules:
- Files carrying `jarvis_id` in their frontmatter are Jarvis-owned and may be rewritten or deleted by sync. Nothing else is ever modified or deleted.
- Skill-area notes are created only when missing. Once they exist the user owns their content (editing them in Obsidian is how the user curates "what I know").
- All writes go through `ObsidianVault.write_note`, which refuses paths that resolve outside the vault.
- Notes are only rewritten when their content changes, so Obsidian does not see churn.

## Portuguese Labels

All structural labels are Brazilian Portuguese, by user request: hub `Cérebro`, fixed branches `Sobre mim` / `Diretrizes` / `Mundo`, sections `O que eu sei`, `Parte de`. They live in one table (`LABELS` in `brain/sync.py`), not scattered literals. Skill names come from the system prompt headings (already Portuguese). User note titles are shown as written.

## Parsing (`brain/vault.py`)

`parse_note(text, rel_path) -> Note`:
- Title: file stem. Folder: parent path relative to the vault.
- Frontmatter: simple YAML subset (`key: value`, inline lists `[a, b]`, dash lists).
- Links: `[[Target]]`, `[[Target|alias]]`, `[[Target#heading]]` → `Target`.
- Tags: frontmatter `tags` plus inline `#tag` (Unicode-aware, not headings).
- Points: bullet / numbered / task items with Markdown stripped and wikilinks reduced to their display text. A note without bullets uses its non-heading paragraph lines. Capped at `MAX_POINTS`.

Dot-folders (`.obsidian`, `.trash`, …) are ignored. Parsing is cached per file by mtime so repeated brain requests stay cheap.

## Brain Map (`brain/brain_map.py`)

`build_brain_map(vault) -> {"nodes", "links", "areas"}` for the UI:
- One node per note, plus one **concept** node per unresolved link target (Obsidian's "ghost" nodes). Link resolution is case-insensitive by title.
- Each node carries `label`, `kind` (`hub`, `area`, `memory`, `note`, `concept`), `area`, `points` (exactly what the note says I know), `backlinks` (titles linking to it), and `degree`.
- Area: frontmatter `area` wins; otherwise a skill-area note is its own area; a node linked from an area inherits the area of its first linker; otherwise the top-level folder; otherwise `Notas`.
- Links are undirected and de-duplicated.

## Recall (`vault_enrichment_parts`)

During reply enrichment (Step 4b in `reply/engine.py`, same gate as the knowledge graph: only when the extractor produced implicit questions with ≥2 content words), the vault is searched with the same content words. Up to 3 user notes (the `JARVIS/Memória` mirror is excluded because the graph search already covers it) are added to the graph parts as `[Obsidian › Title] point; point; …` and flow through the existing memory digest. Fail-open: any vault error yields no parts.

## Sync Lifecycle

- Daemon start (`obsidian_enabled`): seed skill areas, write the hub, mirror the graph. Runs in a background thread; never blocks start-up.
- Graph mutations: a listener schedules a debounced (2 s) re-mirror.
- Brain view request: the brain server runs the same idempotent sync before building the map, so the view is current even when the daemon is off.

## Brain View (desktop, `desktop_app/brain_page.py`)

Served by `desktop_app/brain_server.py` on 127.0.0.1 at `/cerebro` (data at `/api/cerebro`). The brain window opens automatically at launch and from the tray ("🧠 Abrir Cérebro").
- Canvas 2D, no external scripts (works offline). Shape follows Obsidian's graph view: every node spawns in one disc and central gravity balances spatial-grid repulsion + link springs, so the brain settles into a single round sphere (not per-area clusters). Each frame stays ~O(n); the simulation cools but never fully freezes (a gentle drift keeps small graphs alive), large settled graphs stop redrawing until something changes, and dragging re-heats it.
- Labels are hidden by default so the sphere stays clean. Hovering a node names it and every node linked to it (neighbours highlighted, everything else dimmed), and a card shows the area, every point the note holds ("O que eu sei") and its connections. Searching names the matches.
- Click pins the card; search filters by name/point; the (collapsed) legend toggles areas; wheel zooms, drag pans, nodes are draggable.
- Flat dots, no glow except on the hovered node. Concepts (link targets without a note) are neutral grey; notes take their area colour; the hub is amber. Background, link and muted-node colours come from `graph_*` entries in `desktop_app/themes.py`.

## Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `obsidian_enabled` | `true` | Seed, mirror and use the vault |
| `obsidian_vault_path` | `~/Obsidian/JARVIS` | Vault folder, created if missing |
