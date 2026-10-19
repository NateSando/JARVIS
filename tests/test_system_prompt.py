"""Tests for the unified persona system prompt.

The persona is the user's master J.A.R.V.I.S. prompt (Portuguese). It still
goes through ``build_system_prompt`` so a ``{name}`` placeholder, if the
prompt ever uses one, is always resolved.
"""

from jarvis.system_prompt import build_system_prompt


class TestBuildSystemPrompt:
    def test_persona_is_the_master_jarvis_prompt(self):
        prompt = build_system_prompt()
        assert "J.A.R.V.I.S." in prompt

    def test_no_unresolved_placeholders(self):
        for name in ("Jarvis", "Friday", "", "   ", None):
            assert "{name}" not in build_system_prompt(name)  # type: ignore[arg-type]

    def test_blank_name_does_not_break_rendering(self):
        assert build_system_prompt("") == build_system_prompt("Jarvis")
        assert build_system_prompt(None) == build_system_prompt("Jarvis")  # type: ignore[arg-type]

    def test_prompt_defines_skill_areas_for_the_brain(self):
        # The Obsidian brain seeds its skill map from the prompt's ### subsections.
        from jarvis.brain.sync import parse_skill_areas

        areas = parse_skill_areas(build_system_prompt())
        assert areas
        assert all(items for _, items in areas)
