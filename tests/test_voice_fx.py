"""Behaviour tests for the robotic voice effect applied to Kokoro output."""

import numpy as np
import pytest

from jarvis.output.voice_fx import apply_robot_voice


SR = 24000


def _tone(seconds: float = 0.5, freq: float = 220.0, amp: float = 0.5) -> np.ndarray:
    t = np.arange(int(SR * seconds)) / SR
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


@pytest.mark.unit
class TestRobotVoice:
    def test_zero_strength_is_identity(self):
        audio = _tone()
        out = apply_robot_voice(audio, SR, strength=0.0)
        np.testing.assert_allclose(out, audio)

    def test_effect_changes_the_signal(self):
        audio = _tone()
        out = apply_robot_voice(audio, SR, strength=0.6)
        assert not np.allclose(out, audio)

    def test_preserves_length_and_dtype(self):
        audio = _tone(1.3)
        out = apply_robot_voice(audio, SR, strength=1.0)
        assert out.shape == audio.shape
        assert out.dtype == np.float32

    def test_output_never_clips(self):
        loud = _tone(amp=0.99) + _tone(freq=330.0, amp=0.99)
        out = apply_robot_voice(loud, SR, strength=1.0)
        assert np.all(np.isfinite(out))
        assert np.max(np.abs(out)) <= 1.0

    def test_keeps_loudness_in_the_same_ballpark(self):
        audio = _tone()
        out = apply_robot_voice(audio, SR, strength=1.0)
        rms_in = np.sqrt(np.mean(audio ** 2))
        rms_out = np.sqrt(np.mean(out ** 2))
        assert 0.5 * rms_in < rms_out < 2.0 * rms_in

    def test_stronger_effect_deviates_more(self):
        audio = _tone()
        light = apply_robot_voice(audio, SR, strength=0.2)
        heavy = apply_robot_voice(audio, SR, strength=1.0)
        assert np.mean(np.abs(heavy - audio)) > np.mean(np.abs(light - audio))

    def test_handles_empty_and_int16_input(self):
        assert apply_robot_voice(np.zeros(0, dtype=np.float32), SR, 0.5).size == 0
        pcm = (_tone() * 32767).astype(np.int16)
        out = apply_robot_voice(pcm, SR, 0.5)
        assert out.dtype == np.float32
        assert np.max(np.abs(out)) <= 1.0

    def test_strength_is_clamped(self):
        audio = _tone()
        np.testing.assert_allclose(apply_robot_voice(audio, SR, -3.0), audio)
        np.testing.assert_allclose(
            apply_robot_voice(audio, SR, 7.0), apply_robot_voice(audio, SR, 1.0)
        )
