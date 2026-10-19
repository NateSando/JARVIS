"""Kokoro-ONNX TTS: 100% CPU, no PyTorch, multilingual, blendable voices.

Two layers live here:

* ``KokoroSynth``: loads the model (auto-download on first use), resolves the
  voice (single voice or weighted blend) and turns text into float32 samples,
  coloured by the robotic J.A.R.V.I.S. effect from ``jarvis.output.voice_fx``.
  Shared by the daemon engine (``jarvis.output.tts_kokoro``) and the
  standalone voice pipeline below.
* ``KokoroTTS``: the standalone worker used by ``python -m jarvis.voice.cli``.
"""

from __future__ import annotations

import logging
import queue
import re
import threading
from pathlib import Path
from typing import Any, Callable, Optional, Tuple

import numpy as np

from ..output.voice_fx import apply_robot_voice

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Catálogo de vozes do Kokoro-82M v1.0 (prefixo: idioma + f/m)
# ---------------------------------------------------------------------------
KOKORO_VOICES: dict[str, str] = {
    # Português (Brasil)
    "pm_alex": "Português Masculino — alex (grave, firme)",
    "pm_santa": "Português Masculino — santa (encorpado)",
    "pf_dora": "Português Feminina — dora",
    # Inglês Britânico
    "bm_george": "Britânico Masculino — george (formal, mordomo)",
    "bm_daniel": "Britânico Masculino — daniel (sóbrio)",
    "bm_fable": "Britânico Masculino — fable (narrador)",
    "bm_lewis": "Britânico Masculino — lewis (conversacional)",
    "bf_emma": "Britânica Feminina — emma (elegante)",
    "bf_isabella": "Britânica Feminina — isabella (refinada)",
    # Inglês Americano
    "am_onyx": "Americano Masculino — onyx (grave)",
    "am_adam": "Americano Masculino — adam (robusto)",
    "am_michael": "Americano Masculino — michael (neutro)",
    "am_echo": "Americano Masculino — echo (metálico)",
    "am_eric": "Americano Masculino — eric (assertivo)",
    "af_heart": "Americana Feminina — heart (natural)",
    "af_bella": "Americana Feminina — bella (suave)",
    "af_sarah": "Americana Feminina — sarah (clara)",
    "af_nicole": "Americana Feminina — nicole (expressiva)",
    "af_sky": "Americana Feminina — sky (animada)",
    # Espanhol
    "em_alex": "Espanhol Masculino — alex",
    "ef_dora": "Espanhol Feminina — dora",
}

# Voz padrão: base brasileira masculina com um toque britânico de mordomo.
# Precisa ser igual a ``kokoro_voice`` em ``config.get_default_config``.
DEFAULT_VOICE = "pm_alex:0.7,bm_george:0.3"
DEFAULT_LANG = "pt-br"
KOKORO_SAMPLE_RATE = 24000  # Hz — saída nativa do Kokoro

_MODEL_DIR = Path.home() / ".local" / "share" / "jarvis" / "models" / "kokoro"
DEFAULT_MODEL_PATH = _MODEL_DIR / "kokoro-v1.0.int8.onnx"
DEFAULT_VOICES_PATH = _MODEL_DIR / "voices-v1.0.bin"

_MODEL_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.int8.onnx"
_VOICES_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"

# Sentence boundaries across scripts: Latin punctuation needs trailing
# whitespace, CJK full-width punctuation does not.
_SENTENCE_END = re.compile(r"(?<=[.!?;…])\s+|(?<=[。！？；])")
_FIRST_CHUNK_MIN_CHARS = 12
_CHUNK_TARGET_CHARS = 180


def parse_voice_spec(spec: Optional[str]) -> list[tuple[str, float]]:
    """Parse ``"pm_alex"`` or ``"pm_alex:0.7,bm_george:0.3"`` into normalised weights.

    Unknown voices and invalid weights are dropped; an empty result falls back
    to ``DEFAULT_VOICE``.
    """
    parts: list[tuple[str, float]] = []
    for raw in (spec or "").split(","):
        raw = raw.strip()
        if not raw:
            continue
        name, _, weight_txt = raw.partition(":")
        name = name.strip()
        try:
            weight = float(weight_txt) if weight_txt.strip() else 1.0
        except ValueError:
            continue
        if name in KOKORO_VOICES and weight > 0:
            parts.append((name, weight))
    if not parts:
        return parse_voice_spec(DEFAULT_VOICE) if spec != DEFAULT_VOICE else [("pm_alex", 1.0)]
    total = sum(w for _, w in parts)
    return [(n, w / total) for n, w in parts]


def split_for_speech(text: str) -> list[str]:
    """Split text into speakable chunks: a short first chunk, then ~sentence groups.

    The short first chunk lets playback start while the rest is still being
    synthesised. Words are never altered or reordered.
    """
    sentences = [s.strip() for s in _SENTENCE_END.split(text or "") if s and s.strip()]
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        if not current:
            current = sentence
            continue
        if chunks:
            flush = len(current) + 1 + len(sentence) > _CHUNK_TARGET_CHARS
        else:
            flush = len(current) >= _FIRST_CHUNK_MIN_CHARS
        if flush:
            chunks.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}"
    if current:
        chunks.append(current)
    return chunks


class KokoroSynth:
    """Text → float32 samples with Kokoro-82M, voice blending and robot colouring.

    ``backend`` lets callers (tests) inject an object with the
    ``kokoro_onnx.Kokoro`` interface instead of loading the real model.
    """

    def __init__(
        self,
        voice: str = DEFAULT_VOICE,
        speed: float = 1.0,
        lang: str = DEFAULT_LANG,
        model_path: Optional[str] = None,
        voices_path: Optional[str] = None,
        robot_strength: float = 0.0,
        backend: Any = None,
    ) -> None:
        self.voice = voice
        self.speed = speed
        self.lang = lang
        self.robot_strength = robot_strength
        self._model_path = Path(model_path).expanduser() if model_path else DEFAULT_MODEL_PATH
        self._voices_path = Path(voices_path).expanduser() if voices_path else DEFAULT_VOICES_PATH
        self._kokoro = backend
        self._lock = threading.Lock()
        self._style_cache: dict[str, Any] = {}

    @property
    def loaded(self) -> bool:
        return self._kokoro is not None

    def load(self) -> None:
        """Load the model, downloading it on first use. Raises RuntimeError on failure."""
        if self._kokoro is not None:
            return
        with self._lock:
            if self._kokoro is not None:
                return
            try:
                from kokoro_onnx import Kokoro  # type: ignore[import]
            except ImportError as exc:
                raise RuntimeError("kokoro-onnx não instalado. Execute: pip install kokoro-onnx") from exc
            self._model_path.parent.mkdir(parents=True, exist_ok=True)
            _maybe_download(self._model_path, _MODEL_URL, "modelo Kokoro ONNX")
            _maybe_download(self._voices_path, _VOICES_URL, "vozes Kokoro")
            logger.info("⏳ Carregando Kokoro-82M ONNX (CPU)…")
            self._kokoro = Kokoro(str(self._model_path), str(self._voices_path))
            logger.info("✅ Kokoro-82M carregado.")

    def _resolve_voice(self) -> Any:
        blend = parse_voice_spec(self.voice)
        if len(blend) == 1:
            return blend[0][0]
        key = ",".join(f"{n}:{w:.4f}" for n, w in blend)
        style = self._style_cache.get(key)
        if style is None:
            assert self._kokoro is not None
            style = sum(w * np.asarray(self._kokoro.get_voice_style(n), dtype=np.float32) for n, w in blend)
            self._style_cache[key] = style
        return style

    def synthesize(self, text: str) -> Tuple[np.ndarray, int]:
        self.load()
        assert self._kokoro is not None
        samples, sample_rate = self._kokoro.create(
            text, voice=self._resolve_voice(), speed=self.speed, lang=self.lang
        )
        audio = apply_robot_voice(np.asarray(samples), int(sample_rate), self.robot_strength)
        return audio, int(sample_rate)


class KokoroTTS:
    """Worker de TTS standalone (CLI de voz): fila + síntese + reprodução interrompível."""

    def __init__(
        self,
        voice: str = DEFAULT_VOICE,
        speed: float = 1.0,
        lang: str = DEFAULT_LANG,
        model_path: Optional[str] = None,
        voices_path: Optional[str] = None,
        robot_strength: float = 0.35,
    ) -> None:
        self._synth = KokoroSynth(
            voice=voice, speed=speed, lang=lang, model_path=model_path,
            voices_path=voices_path, robot_strength=robot_strength,
        )
        self._queue: queue.Queue[Optional[Tuple[str, Optional[Callable[[], None]]]]] = queue.Queue()
        self._worker: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._interrupt_event = threading.Event()
        self._is_speaking = threading.Event()

    @property
    def voice(self) -> str:
        return self._synth.voice

    def start(self) -> None:
        if self._worker is not None:
            return
        self._stop_event.clear()
        threading.Thread(target=self._preload, daemon=True, name="kokoro-init").start()
        self._worker = threading.Thread(target=self._run, daemon=True, name="kokoro-worker")
        self._worker.start()
        logger.info("🔊 KokoroTTS worker iniciado.")

    def stop(self) -> None:
        self.interrupt()
        self._stop_event.set()
        self._queue.put_nowait(None)
        if self._worker is not None:
            self._worker.join(timeout=3.0)
            self._worker = None
        logger.info("KokoroTTS worker parado.")

    def speak(self, text: str, on_done: Optional[Callable[[], None]] = None) -> None:
        if not text.strip():
            return
        if self._worker is None:
            self.start()
        self._interrupt_event.clear()
        self._queue.put_nowait((text, on_done))

    def interrupt(self) -> None:
        self._interrupt_event.set()

    @property
    def is_speaking(self) -> bool:
        return self._is_speaking.is_set()

    def set_voice(self, voice: str) -> None:
        """Troca a voz (ou mistura) em tempo de execução."""
        self._synth.voice = voice
        logger.info("Voz alterada para: %s", voice)

    def available_voices(self) -> dict[str, str]:
        return dict(KOKORO_VOICES)

    def _preload(self) -> None:
        try:
            self._synth.load()
        except Exception as exc:
            logger.error("KokoroTTS não disponível: %s", exc)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                item = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if item is None:
                break
            text, on_done = item
            self._is_speaking.set()
            try:
                for chunk in split_for_speech(text):
                    if self._interrupt_event.is_set():
                        break
                    samples, sample_rate = self._synth.synthesize(chunk)
                    play_blocking(samples, sample_rate, self._interrupt_event)
                interrupted = self._interrupt_event.is_set()
            except Exception as exc:
                logger.error("Erro na síntese Kokoro: %s", exc, exc_info=True)
                interrupted = True
            finally:
                self._is_speaking.clear()
            if not interrupted and on_done is not None:
                try:
                    on_done()
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# Funções utilitárias
# ---------------------------------------------------------------------------


def play_blocking(samples: np.ndarray, sample_rate: int, interrupt_event: threading.Event) -> None:
    """Reproduz áudio PCM float32 bloqueando até o fim ou interrupção."""
    try:
        import sounddevice as sd  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError("sounddevice não instalado. Execute: pip install sounddevice") from exc

    audio = np.asarray(samples, dtype=np.float32)
    if audio.ndim == 2:
        audio = audio.mean(axis=1)

    block_size = int(sample_rate * 0.05)  # blocos de 50 ms para interrupção responsiva
    with sd.OutputStream(samplerate=sample_rate, channels=1, dtype="float32", blocksize=block_size) as stream:
        offset = 0
        while offset < len(audio):
            if interrupt_event.is_set():
                break
            stream.write(audio[offset : offset + block_size])
            offset += block_size


def _maybe_download(path: Path, url: str, label: str) -> None:
    """Baixa um arquivo se ainda não existir (escrita atômica via .tmp)."""
    if path.exists():
        return
    logger.info("📥 Baixando %s (download único)…", label)
    import requests  # type: ignore[import]

    tmp = path.with_suffix(".tmp")
    try:
        with requests.get(url, stream=True, timeout=120) as resp:
            resp.raise_for_status()
            with open(tmp, "wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    f.write(chunk)
        tmp.replace(path)
        logger.info("✅ %s baixado: %s", label, path.name)
    except Exception as exc:
        logger.error("❌ Falha ao baixar %s: %s", label, exc)
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"Falha ao baixar {label}: {exc}") from exc
