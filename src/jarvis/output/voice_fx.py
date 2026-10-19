"""Robotic "J.A.R.V.I.S." voice colouring for synthesised speech.

Pure numpy post-processing applied to TTS output before playback. The goal is
the film assistant's timbre: a natural voice heard through a slightly
metallic, synthetic channel. Not a Dalek: intelligibility always wins.

Chain (all scaled by ``strength`` in [0, 1]):
1. Metallic comb: a few decaying echoes at a ~5 ms spacing give the hollow,
   resonant "voice in a machine" ring.
2. Doubler: one short (~11 ms) copy adds the thickened, processed body.
3. Ring modulation: a small mix of the signal multiplied by a carrier adds
   the faint synthetic buzz.
4. Loudness match to the dry input, then a peak guard so nothing clips.
"""

from __future__ import annotations

import numpy as np

_COMB_DELAY_S = 0.005
_COMB_TAPS = 3
_DOUBLER_DELAY_S = 0.011
_RING_CARRIER_HZ = 160.0
_PEAK_CEILING = 0.98


def _as_float(samples: np.ndarray) -> np.ndarray:
    audio = np.asarray(samples)
    if audio.dtype == np.int16:
        audio = audio.astype(np.float32) / 32768.0
    audio = audio.astype(np.float32, copy=False)
    if audio.ndim == 2:
        audio = audio.mean(axis=1).astype(np.float32)
    return audio


def _delayed(audio: np.ndarray, delay: int) -> np.ndarray:
    out = np.zeros_like(audio)
    if 0 < delay < audio.size:
        out[delay:] = audio[:-delay]
    return out


def apply_robot_voice(samples: np.ndarray, sample_rate: int, strength: float) -> np.ndarray:
    """Return a robot-coloured copy of ``samples`` (float32, same length).

    ``strength`` is clamped to [0, 1]; 0 returns the input unchanged.
    """
    audio = _as_float(samples)
    strength = float(min(max(strength, 0.0), 1.0))
    if strength == 0.0 or audio.size == 0:
        return audio.copy()

    wet = audio.copy()

    comb_delay = max(1, int(sample_rate * _COMB_DELAY_S))
    gain = 0.55 * strength
    for tap in range(1, _COMB_TAPS + 1):
        wet += (gain ** tap) * _delayed(audio, comb_delay * tap)

    wet += 0.25 * strength * _delayed(audio, int(sample_rate * _DOUBLER_DELAY_S))

    t = np.arange(audio.size, dtype=np.float32) / float(sample_rate)
    carrier = np.sin(2.0 * np.pi * _RING_CARRIER_HZ * t).astype(np.float32)
    wet += 0.18 * strength * audio * carrier

    rms_dry = float(np.sqrt(np.mean(audio ** 2)))
    rms_wet = float(np.sqrt(np.mean(wet ** 2)))
    if rms_wet > 1e-9 and rms_dry > 0.0:
        wet *= rms_dry / rms_wet

    peak = float(np.max(np.abs(wet)))
    if peak > _PEAK_CEILING:
        wet *= _PEAK_CEILING / peak

    return wet.astype(np.float32, copy=False)
