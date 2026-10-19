"""Behaviour tests for the Obsidian brain: vault parsing, sync, brain map, recall."""

from __future__ import annotations

import pytest

from jarvis.brain.vault import ObsidianVault, parse_note
from jarvis.brain.sync import (
    HUB_PATH,
    LABELS,
    MEMORY_DIR,
    SKILLS_DIR,
    seed_skill_map,
    sync_brain,
    sync_memory_to_vault,
)
from jarvis.brain.brain_map import build_brain_map
from jarvis.brain import vault_enrichment_parts
from jarvis.memory.graph import GraphMemoryStore
from jarvis.system_prompt import build_system_prompt


PROMPT = """
## 1. Identidade
Texto qualquer.

### 9.1 Inteligência Artificial

Domínio em:

- agentes autônomos;
- LangGraph;
- uma frase bem longa que descreve uma habilidade complexa demais para virar um link.

Ao propor arquitetura, considere:

- custo;

### 9.2 Segurança

- OWASP;
- pentest.

## 10. Outra seção
"""


@pytest.fixture
def vault(tmp_path):
    return ObsidianVault(tmp_path / "vault")


@pytest.fixture
def store(tmp_path):
    s = GraphMemoryStore(str(tmp_path / "mem.db"))
    yield s
    s.close()


@pytest.mark.unit
class TestParseNote:
    def test_extracts_links_tags_points_and_frontmatter(self):
        text = (
            "---\ntags: [estudo, ia]\narea: Inteligência Artificial\n---\n"
            "# RAG\n\nUso #python no dia a dia.\n\n"
            "- sei montar [[Embeddings|vetores]] e [[Reranking#Modelos]]\n"
            "* **chunking** semântico\n"
            "1. avaliação com [link](http://x)\n"
            "- [x] tarefa feita\n"
        )
        note = parse_note(text, "Estudos/RAG.md")
        assert note.title == "RAG"
        assert note.folder == "Estudos"
        assert note.links == ["Embeddings", "Reranking"]
        assert {"estudo", "ia", "python"} <= set(note.tags)
        assert note.frontmatter["area"] == "Inteligência Artificial"
        assert note.points == [
            "sei montar vetores e Reranking",
            "chunking semântico",
            "avaliação com link",
            "tarefa feita",
        ]

    def test_paragraphs_become_points_when_there_are_no_bullets(self):
        note = parse_note("# Título\n\nPrimeira ideia.\n\nSegunda ideia.\n", "N.md")
        assert note.points == ["Primeira ideia.", "Segunda ideia."]

    def test_headings_are_not_tags(self):
        note = parse_note("# Cabeçalho\n## Outro\n", "N.md")
        assert note.tags == []


@pytest.mark.unit
class TestVault:
    def test_ignores_dot_folders_and_non_markdown(self, vault):
        vault.write_note("A.md", "- um")
        (vault.root / ".obsidian").mkdir()
        (vault.root / ".obsidian" / "x.md").write_text("- oculto")
        (vault.root / "img.png").write_bytes(b"x")
        assert [n.title for n in vault.notes()] == ["A"]

    def test_refuses_writes_outside_the_vault(self, vault):
        with pytest.raises(ValueError):
            vault.write_note("../fora.md", "x")

    def test_unchanged_content_is_not_rewritten(self, vault):
        assert vault.write_note("A.md", "- um") is True
        assert vault.write_note("A.md", "- um") is False
        assert vault.write_note("A.md", "- dois") is True

    def test_search_ranks_title_and_points(self, vault):
        vault.write_note("Kubernetes.md", "- deploy de pods\n- helm charts")
        vault.write_note("Culinária.md", "- receita de pão")
        vault.write_note("Infra.md", "- uso kubernetes com helm")
        hits = vault.search(["kubernetes", "helm"], limit=5)
        assert [h.note.title for h in hits][:2] == ["Kubernetes", "Infra"]
        assert all(h.note.title != "Culinária" for h in hits)


@pytest.mark.unit
class TestSkillSeed:
    def test_every_subsection_becomes_an_area_with_its_first_list(self, vault):
        created = seed_skill_map(vault, PROMPT)
        assert created == 2
        note = vault.get(f"{SKILLS_DIR}/Inteligência Artificial.md")
        assert note is not None
        assert "LangGraph" in note.links
        assert "agentes autônomos" in note.links
        # Long items stay as plain points, and the second list is not included
        assert any(p.startswith("uma frase bem longa") for p in note.points)
        assert all("custo" not in p for p in note.points)

    def test_seed_never_overwrites_user_edits(self, vault):
        seed_skill_map(vault, PROMPT)
        path = f"{SKILLS_DIR}/Segurança.md"
        vault.write_note(path, "- editado pelo usuário")
        assert seed_skill_map(vault, PROMPT) == 0
        assert vault.get(path).points == ["editado pelo usuário"]

    def test_real_master_prompt_yields_areas(self, vault):
        assert seed_skill_map(vault, build_system_prompt()) > 0


@pytest.mark.unit
class TestMemoryMirror:
    def test_nodes_become_linked_notes_with_portuguese_branch_names(self, vault, store):
        child = store.create_node("Programação", "Linguagens", data="Usa Python\nGosta de Rust", parent_id="user")
        result = sync_memory_to_vault(store, vault)
        assert result.written > 0
        user_note = vault.get(f"{MEMORY_DIR}/{LABELS['user']}.md")
        assert user_note is not None
        prog = vault.get(f"{MEMORY_DIR}/Programação.md")
        assert prog.points == ["Usa Python", "Gosta de Rust"]
        assert LABELS["user"] in prog.links
        assert prog.frontmatter["jarvis_id"] == child.id

    def test_deleted_nodes_are_removed_but_user_files_are_kept(self, vault, store):
        node = store.create_node("Temporário", "x", data="algo", parent_id="world")
        sync_memory_to_vault(store, vault)
        vault.write_note(f"{MEMORY_DIR}/Minha nota.md", "- minha")
        store.delete_node(node.id)
        result = sync_memory_to_vault(store, vault)
        assert result.removed == 1
        assert vault.get(f"{MEMORY_DIR}/Temporário.md") is None
        assert vault.get(f"{MEMORY_DIR}/Minha nota.md") is not None

    def test_name_collisions_get_distinct_files(self, vault, store):
        store.create_node("Viagens", "a", data="Japão", parent_id="user")
        store.create_node("Viagens", "b", data="Lisboa", parent_id="world")
        sync_memory_to_vault(store, vault)
        mirrored = [n for n in vault.notes() if n.folder == MEMORY_DIR and n.title.startswith("Viagens")]
        assert len(mirrored) == 2


@pytest.mark.unit
class TestBrainMap:
    def test_full_brain_has_hub_areas_concepts_and_points(self, vault, store):
        store.create_node("Programação", "Linguagens", data="Usa Python", parent_id="user")
        sync_brain(vault, store, PROMPT)
        brain = build_brain_map(vault)
        by_label = {n["label"]: n for n in brain["nodes"]}

        assert by_label[LABELS["hub"]]["kind"] == "hub"
        ai = by_label["Inteligência Artificial"]
        assert ai["kind"] == "area"
        assert "LangGraph" in ai["points"]
        concept = by_label["LangGraph"]
        assert concept["kind"] == "concept"
        assert concept["area"] == "Inteligência Artificial"
        assert "Inteligência Artificial" in concept["backlinks"]
        assert by_label["Programação"]["points"] == ["Usa Python"]

        area_names = {a["name"] for a in brain["areas"]}
        assert {"Inteligência Artificial", "Segurança"} <= area_names

    def test_links_are_undirected_and_unique(self, vault):
        vault.write_note("A.md", "- [[B]] e [[B]]")
        vault.write_note("B.md", "- volta para [[A]]")
        brain = build_brain_map(vault)
        assert len(brain["links"]) == 1

    def test_link_resolution_is_case_insensitive(self, vault):
        vault.write_note("Docker.md", "- containers")
        vault.write_note("Infra.md", "- [[docker]]")
        brain = build_brain_map(vault)
        assert sum(1 for n in brain["nodes"] if n["label"].lower() == "docker") == 1

    def test_user_notes_take_area_from_frontmatter_or_folder(self, vault):
        vault.write_note("Estudos/Redes.md", "- TCP")
        vault.write_note("Solta.md", "---\narea: Música\n---\n- violão")
        by_label = {n["label"]: n for n in build_brain_map(vault)["nodes"]}
        assert by_label["Redes"]["area"] == "Estudos"
        assert by_label["Solta"]["area"] == "Música"


@pytest.mark.unit
class TestRecall:
    def test_user_notes_are_returned_for_matching_words(self, vault):
        vault.write_note("Kubernetes.md", "- deploy com helm\n- pods")
        parts = vault_enrichment_parts(str(vault.root), ["kubernetes", "helm"])
        assert len(parts) == 1
        assert parts[0].startswith("[Obsidian › Kubernetes]")
        assert "deploy com helm" in parts[0]

    def test_memory_mirror_is_excluded(self, vault, store):
        store.create_node("Kubernetes", "x", data="usa helm", parent_id="user")
        sync_memory_to_vault(store, vault)
        assert vault_enrichment_parts(str(vault.root), ["kubernetes", "helm"]) == []

    def test_missing_vault_fails_open(self, tmp_path):
        assert vault_enrichment_parts(str(tmp_path / "nada"), ["a", "b"]) == []


@pytest.mark.unit
class TestBrainSyncer:
    def test_graph_writes_reach_the_vault_after_debounce(self, tmp_path, monkeypatch):
        import time
        from jarvis.brain.sync import BrainSyncer
        from jarvis.memory.graph import (
            register_graph_mutation_listener,
            unregister_graph_mutation_listener,
        )

        db = str(tmp_path / "mem.db")
        syncer = BrainSyncer(str(tmp_path / "vault"), db, PROMPT)
        monkeypatch.setattr(BrainSyncer, "DEBOUNCE_SEC", 0.05)
        syncer.sync_now()
        assert syncer.vault.get(HUB_PATH) is not None

        register_graph_mutation_listener(syncer.on_graph_mutation)
        try:
            store = GraphMemoryStore(db)
            store.create_node("Música", "Gostos", data="Toca violão", parent_id="user")
            store.close()
            deadline = time.time() + 3
            while time.time() < deadline and syncer.vault.get(f"{MEMORY_DIR}/Música.md") is None:
                time.sleep(0.02)
            assert syncer.vault.get(f"{MEMORY_DIR}/Música.md").points == ["Toca violão"]
        finally:
            unregister_graph_mutation_listener(syncer.on_graph_mutation)
