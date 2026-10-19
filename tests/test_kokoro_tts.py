"""Behaviour tests for the Kokoro voice stack (voice specs, synth, daemon engine).

The real Kokoro model is never loaded here: a fake backend stands in for
``kokoro_onnx.Kokoro`` and a fake player records what would have been played.
"""

from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from jarvis.voice.tts_kokoro import (
    KOKORO_VOICES,
    KokoroSynth,
    parse_voice_spec,
    split_for_speech,
)
from jarvis.output.tts_kokoro import KokoroTTSEngine
from jarvis.output.tts import create_tts_engine
from jarvis.config import get_default_config


SR = 24000


class FakeKokoro:
    """Stand-in for kokoro_onnx.Kokoro: 10 ms of tone per character."""

    def __init__(self):
        self.calls: list[dict] = []
        self.styles = {
            name: np.full((4, 256), float(i + 1), dtype=np.float32)
            for i, name in enumerate(KOKORO_VOICES)
        }

    def get_voice_style(self, name):
        return self.styles[name]

    def create(self, text, voice, speed, lang):
        self.calls.append({"text": text, "voice": voice, "speed": speed, "lang": lang})
        n = int(SR * 0.01) * max(1, len(text))
        t = np.arange(n) / SR
        return (0.3 * np.sin(2 * np.pi * 200 * t)).astype(np.float32), SR


class RecordingPlayer:
    def __init__(self, delay: float = 0.0):
        self.played: list[np.ndarray] = []
        self.delay = delay

    def __call__(self, samples, sample_rate, interrupt_event):
        if interrupt_event.is_set():
            return
        if self.delay:
            interrupt_event.wait(self.delay)
            if interrupt_event.is_set():
                return
        self.played.append(samples)


def _engine(player=None, robot=0.0, voice="pm_alex", **kw):
    synth = KokoroSynth(voice=voice, lang="pt-br", robot_strength=robot, backend=FakeKokoro())
    return KokoroTTSEngine(synth=synth, player=player or RecordingPlayer(), **kw)


def _wait(predicate, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return False


@pytest.mark.unit
class TestVoiceSpec:
    def test_single_voice(self):
        assert parse_voice_spec("pm_alex") == [("pm_alex", 1.0)]

    def test_blend_is_normalised(self):
        blend = parse_voice_spec("pm_alex:3, bm_george:1")
        assert [n for n, _ in blend] == ["pm_alex", "bm_george"]
        assert sum(w for _, w in blend) == pytest.approx(1.0)
        assert dict(blend)["pm_alex"] == pytest.approx(0.75)

    def test_unknown_voices_are_dropped(self):
        assert parse_voice_spec("nope:0.5,pm_alex:0.5") == [("pm_alex", 1.0)]

    def test_garbage_falls_back_to_default(self):
        default = get_default_config()["kokoro_voice"]
        assert parse_voice_spec("") == parse_voice_spec(default)
        assert parse_voice_spec("nope") == parse_voice_spec(default)

    def test_default_voice_is_male(self):
        default = get_default_config()["kokoro_voice"]
        for name, _ in parse_voice_spec(default):
            assert name[1] == "m", f"{name} is not a male Kokoro voice"


@pytest.mark.unit
class TestSplitForSpeech:
    def test_keeps_all_words_in_order(self):
        text = "Bom dia, Senhor. Todos os protocolos foram reativados! Deseja continuar? Sim."
        chunks = split_for_speech(text)
        assert " ".join(chunks).split() == text.split()

    def test_first_chunk_is_short_for_low_latency(self):
        text = "Bom dia, Senhor. " + "Esta é uma frase consideravelmente longa sobre o sistema. " * 6
        chunks = split_for_speech(text)
        assert len(chunks) > 1
        assert len(chunks[0]) < len(text) / 3

    def test_non_latin_scripts_split_too(self):
        chunks = split_for_speech("こんにちは。元気ですか？はい。" * 10)
        assert len(chunks) > 1

    def test_empty_text(self):
        assert split_for_speech("   ") == []


@pytest.mark.unit
class TestKokoroSynth:
    def test_blend_passes_a_mixed_style_vector(self):
        backend = FakeKokoro()
        synth = KokoroSynth(voice="pm_alex:0.5,bm_george:0.5", backend=backend)
        synth.synthesize("Olá")
        style = backend.calls[-1]["voice"]
        expected = 0.5 * backend.styles["pm_alex"] + 0.5 * backend.styles["bm_george"]
        np.testing.assert_allclose(style, expected)

    def test_single_voice_passes_the_name(self):
        backend = FakeKokoro()
        KokoroSynth(voice="pm_alex", backend=backend).synthesize("Olá")
        assert backend.calls[-1]["voice"] == "pm_alex"

    def test_language_and_speed_reach_the_model(self):
        backend = FakeKokoro()
        KokoroSynth(voice="pm_alex", lang="pt-br", speed=1.2, backend=backend).synthesize("Olá")
        assert backend.calls[-1]["lang"] == "pt-br"
        assert backend.calls[-1]["speed"] == pytest.approx(1.2)

    def test_robot_effect_colours_output(self):
        dry, _ = KokoroSynth(voice="pm_alex", robot_strength=0.0, backend=FakeKokoro()).synthesize("Olá")
        wet, _ = KokoroSynth(voice="pm_alex", robot_strength=0.8, backend=FakeKokoro()).synthesize("Olá")
        assert dry.shape == wet.shape
        assert not np.allclose(dry, wet)


@pytest.mark.unit
class TestKokoroEngine:
    def test_speaks_every_chunk_and_reports_completion(self):
        player = RecordingPlayer()
        engine = _engine(player)
        done = threading.Event()
        durations: list[float] = []
        text = "Bom dia, Senhor. " + "Sistemas nominais e prontos para uso. " * 5
        engine.speak(text, completion_callback=done.set, duration_callback=durations.append)
        assert done.wait(3.0)
        assert len(player.played) == len(split_for_speech(text))
        total = sum(len(s) for s in player.played) / SR
        assert durations and durations[-1] == pytest.approx(total)
        assert durations == sorted(durations)
        engine.stop()

    def test_interrupt_stops_playback_without_completion(self):
        player = RecordingPlayer(delay=0.5)
        engine = _engine(player)
        done = threading.Event()
        engine.speak("Primeira frase aqui. " * 20, completion_callback=done.set)
        assert _wait(engine.is_speaking)
        engine.interrupt()
        assert _wait(lambda: not engine.is_speaking())
        assert not done.is_set()
        assert len(player.played) < len(split_for_speech("Primeira frase aqui. " * 20))
        engine.stop()

    def test_tracks_last_spoken_text_for_echo_guard(self):
        engine = _engine()
        done = threading.Event()
        engine.speak("Olá, Senhor.", completion_callback=done.set)
        assert done.wait(3.0)
        assert "Senhor" in engine.get_last_spoken_text()
        engine.stop()

    def test_disabled_engine_is_silent(self):
        player = RecordingPlayer()
        engine = _engine(player, enabled=False)
        engine.speak("Olá")
        time.sleep(0.1)
        assert player.played == []

    def test_markdown_is_not_read_aloud(self):
        backend = FakeKokoro()
        synth = KokoroSynth(voice="pm_alex", backend=backend)
        engine = KokoroTTSEngine(synth=synth, player=RecordingPlayer())
        done = threading.Event()
        engine.speak("**Atenção**, Senhor.", completion_callback=done.set)
        assert done.wait(3.0)
        assert all("*" not in c["text"] for c in backend.calls)
        engine.stop()


@pytest.mark.unit
class TestFactoryAndDefaults:
    def test_factory_builds_kokoro_engine(self):
        engine = create_tts_engine(enabled=False)
        assert isinstance(engine, KokoroTTSEngine)

    def test_kokoro_setting_survives_load(self, tmp_path, monkeypatch):
        import json
        from jarvis import config as cfg_mod

        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({"tts_engine": "piper", "kokoro_robot_effect": 0.7}))
        monkeypatch.setenv("JARVIS_CONFIG_PATH", str(cfg_file))
        settings = cfg_mod.load_settings()
        # Legacy engine choices are ignored: Kokoro is the only engine.
        assert not hasattr(settings, "tts_engine")
        assert settings.kokoro_robot_effect == pytest.approx(0.7)
