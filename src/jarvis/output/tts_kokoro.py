"""Kokoro TTS engine for the daemon: streaming, interruptible, robot-coloured.

Standard engine interface (start/stop/speak/interrupt, completion +
duration callbacks, loopback-guard helpers) used by the voice listener.

Latency: the reply is split into chunks (short first chunk, then sentence
groups). A producer thread synthesises chunk N+1 while chunk N plays, so the
first words are heard after one short synthesis instead of the whole reply.
"""

from __future__ import annotations

import queue
import sys
import threading
from typing import Callable, Optional

import numpy as np

from ..debug import debug_log
from ..voice.tts_kokoro import (
    DEFAULT_LANG,
    DEFAULT_VOICE,
    KokoroSynth,
    play_blocking,
    split_for_speech,
)
from .tts import _preprocess_for_speech

Player = Callable[[np.ndarray, int, threading.Event], None]


class KokoroTTSEngine:
    """Daemon TTS engine backed by Kokoro-82M (CPU) with the J.A.R.V.I.S. voice."""

    def __init__(
        self,
        enabled: bool = True,
        voice: str = DEFAULT_VOICE,
        speed: float = 1.0,
        lang: str = DEFAULT_LANG,
        model_path: Optional[str] = None,
        voices_path: Optional[str] = None,
        robot_strength: float = 0.35,
        synth: Optional[KokoroSynth] = None,
        player: Optional[Player] = None,
    ) -> None:
        self.enabled = enabled
        self._synth = synth or KokoroSynth(
            voice=voice, speed=speed, lang=lang, model_path=model_path,
            voices_path=voices_path, robot_strength=robot_strength,
        )
        self._player: Player = player or play_blocking

        self._q: queue.Queue[str] = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._should_interrupt = threading.Event()
        self._is_speaking = threading.Event()
        self._last_spoken_text = ""
        self._completion_callback: Optional[Callable[[], None]] = None
        self._duration_callback: Optional[Callable[[float], None]] = None
        self._init_error: Optional[str] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        if not self.enabled or self._thread is not None:
            return
        # Load eagerly so the one-time model download happens at startup.
        try:
            self._synth.load()
        except Exception as e:
            self._init_error = str(e)
            print(f"  ⚠️ Kokoro TTS: {e}", file=sys.stderr, flush=True)
            debug_log(f"kokoro init failed: {e}", "tts")
        self._thread = threading.Thread(target=self._run, daemon=True, name="kokoro-tts")
        self._thread.start()

    def stop(self) -> None:
        if self._thread is None:
            return
        self.interrupt()
        self._stop.set()
        self._q.put_nowait("")
        self._thread.join(timeout=2.0)
        self._thread = None
        self._stop.clear()

    def speak(self, text: str, completion_callback: Optional[Callable[[], None]] = None,
              duration_callback: Optional[Callable[[float], None]] = None) -> None:
        if not self.enabled or not text.strip():
            return
        if self._thread is None:
            self.start()
        self._completion_callback = completion_callback
        self._duration_callback = duration_callback
        self._q.put_nowait(_preprocess_for_speech(text))

    def interrupt(self) -> None:
        self._should_interrupt.set()

    # ------------------------------------------------------------------
    # Worker
    # ------------------------------------------------------------------

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                text = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            if not text:
                continue
            try:
                self._speak_once(text)
            except Exception as e:
                debug_log(f"kokoro speak error: {e}", "tts")

    def _speak_once(self, text: str) -> None:
        self._should_interrupt.clear()
        self._is_speaking.set()
        self._last_spoken_text = text
        interrupted = False

        chunks = split_for_speech(text)
        debug_log(f"kokoro speaking {len(chunks)} chunk(s), {len(text)} chars", "tts")
        ready: queue.Queue = queue.Queue(maxsize=2)
        total_duration = [0.0]

        def produce() -> None:
            try:
                for chunk in chunks:
                    if self._should_interrupt.is_set():
                        break
                    samples, sample_rate = self._synth.synthesize(chunk)
                    total_duration[0] += len(samples) / float(sample_rate)
                    if self._duration_callback is not None:
                        try:
                            self._duration_callback(total_duration[0])
                        except Exception as e:
                            debug_log(f"kokoro duration callback error: {e}", "tts")
                    ready.put((samples, sample_rate))
            except Exception as e:
                debug_log(f"kokoro synthesis error: {e}", "tts")
                print(f"  ⚠️ Kokoro TTS error: {e}", flush=True)
                ready.put(e)
                return
            ready.put(None)

        producer = threading.Thread(target=produce, daemon=True, name="kokoro-synth")
        producer.start()
        try:
            while True:
                try:
                    item = ready.get(timeout=0.1)
                except queue.Empty:
                    if self._should_interrupt.is_set():
                        interrupted = True
                        break
                    continue
                if item is None:
                    break
                if isinstance(item, Exception):
                    interrupted = True
                    break
                if self._should_interrupt.is_set():
                    interrupted = True
                    break
                self._player(item[0], item[1], self._should_interrupt)
            interrupted = interrupted or self._should_interrupt.is_set()
        finally:
            # Unblock a producer waiting on a full queue so it can exit.
            while producer.is_alive():
                try:
                    ready.get(timeout=0.05)
                except queue.Empty:
                    pass
            self._is_speaking.clear()
            debug_log(f"kokoro done (interrupted={interrupted}, audio={total_duration[0]:.2f}s)", "tts")
            if self._completion_callback is not None and not interrupted:
                try:
                    self._completion_callback()
                except Exception as e:
                    print(f"  ⚠️ Kokoro TTS completion callback error: {e}", flush=True)
            self._completion_callback = None

    # Loopback guard helpers (same interface as the other engines)
    def is_speaking(self) -> bool:
        return self._is_speaking.is_set()

    def get_last_spoken_text(self) -> str:
        return self._last_spoken_text
