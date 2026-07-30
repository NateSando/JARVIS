"""Captura de microfone e reprodução de áudio com sounddevice."""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Optional

import numpy as np

logger = logging.getLogger(__name__)

# Taxa de amostragem preferida pelo Faster-Whisper
CAPTURE_SAMPLE_RATE = 16000
CAPTURE_CHANNELS = 1
CAPTURE_DTYPE = "float32"


class AudioCapture:
    """Captura contínua do microfone com detecção de silêncio (VAD simples por energia).

    Uso típico::

        cap = AudioCapture(on_speech=my_callback)
        cap.start()
        # … aguarda …
        cap.stop()

    O callback ``on_speech`` recebe um ``np.ndarray float32`` com o trecho de fala
    detectado, pronto para envio ao Faster-Whisper.
    """

    def __init__(
        self,
        on_speech: Callable[[np.ndarray], None],
        sample_rate: int = CAPTURE_SAMPLE_RATE,
        silence_threshold: float = 0.015,
        silence_duration_s: float = 1.2,
        max_duration_s: float = 30.0,
        pre_roll_s: float = 0.3,
        device: Optional[str | int] = None,
    ) -> None:
        self.on_speech = on_speech
        self.sample_rate = sample_rate
        self.silence_threshold = silence_threshold
        self.silence_duration_s = silence_duration_s
        self.max_duration_s = max_duration_s
        self.pre_roll_s = pre_roll_s
        self.device = device

        self._stream: Optional[Any] = None
        self._running = threading.Event()
        self._buffer: list[np.ndarray] = []
        self._pre_roll: list[np.ndarray] = []
        self._recording = False
        self._silent_frames = 0
        self._total_frames = 0

        # Calculados em start() quando sample_rate é confirmado
        self._block_size = 1024
        self._silence_blocks: int = 0
        self._max_blocks: int = 0
        self._pre_roll_blocks: int = 0

    def start(self) -> None:
        try:
            import sounddevice as sd  # type: ignore[import]
        except ImportError as exc:
            raise RuntimeError(
                "sounddevice não instalado. Execute: pip install sounddevice"
            ) from exc

        self._block_size = int(self.sample_rate * 0.064)  # ~64 ms
        self._silence_blocks = int(
            self.silence_duration_s * self.sample_rate / self._block_size
        )
        self._max_blocks = int(
            self.max_duration_s * self.sample_rate / self._block_size
        )
        self._pre_roll_blocks = int(
            self.pre_roll_s * self.sample_rate / self._block_size
        )

        self._running.set()
        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=CAPTURE_CHANNELS,
            dtype=CAPTURE_DTYPE,
            blocksize=self._block_size,
            device=self.device,
            callback=self._audio_callback,
        )
        self._stream.start()
        logger.info("🎙️ Microfone aberto (%.0f Hz, dispositivo=%s).", self.sample_rate, self.device)

    def stop(self) -> None:
        self._running.clear()
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        logger.info("Microfone fechado.")

    def _audio_callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info: object,
        status: object,
    ) -> None:
        if not self._running.is_set():
            return

        block = indata[:, 0].copy()  # mono
        energy = float(np.sqrt(np.mean(block ** 2)))
        is_speech = energy > self.silence_threshold

        if not self._recording:
            # Manter pre-roll circular
            self._pre_roll.append(block)
            if len(self._pre_roll) > self._pre_roll_blocks:
                self._pre_roll.pop(0)
            if is_speech:
                logger.debug("▶️  Fala detectada (energy=%.4f)", energy)
                self._recording = True
                self._buffer = list(self._pre_roll)
                self._silent_frames = 0
                self._total_frames = 0
        else:
            self._buffer.append(block)
            self._total_frames += 1

            if is_speech:
                self._silent_frames = 0
            else:
                self._silent_frames += 1

            if (
                self._silent_frames >= self._silence_blocks
                or self._total_frames >= self._max_blocks
            ):
                reason = "silêncio" if self._silent_frames >= self._silence_blocks else "tempo máximo"
                logger.debug("⏹️  Fim da fala (%s), %d blocos.", reason, self._total_frames)
                audio = np.concatenate(self._buffer, axis=0)
                self._recording = False
                self._buffer = []
                self._pre_roll = []
                # Dispara callback em thread separada para não bloquear o stream
                threading.Thread(
                    target=self.on_speech,
                    args=(audio,),
                    daemon=True,
                    name="on-speech",
                ).start()


def play_audio(samples: np.ndarray, sample_rate: int) -> None:
    """Reproduz áudio numpy float32 de forma síncrona (bloqueante)."""
    try:
        import sounddevice as sd  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError(
            "sounddevice não instalado. Execute: pip install sounddevice"
        ) from exc

    audio = np.asarray(samples, dtype=np.float32)
    if audio.ndim == 2:
        audio = audio.mean(axis=1)
    sd.play(audio, samplerate=sample_rate, blocking=True)


def list_input_devices() -> list[dict[str, object]]:
    """Lista dispositivos de entrada disponíveis."""
    try:
        import sounddevice as sd  # type: ignore[import]

        devices = sd.query_devices()
        return [
            {"index": i, "name": d["name"], "channels": d["max_input_channels"]}
            for i, d in enumerate(devices)  # type: ignore[arg-type]
            if d["max_input_channels"] > 0
        ]
    except Exception:
        return []
