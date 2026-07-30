"""Wrapper para Kokoro-ONNX TTS — 100% CPU, sem PyTorch, múltiplas vozes."""

from __future__ import annotations

import logging
import queue
import threading
from pathlib import Path
from typing import Callable, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Catálogo de vozes disponíveis no Kokoro-82M
# ---------------------------------------------------------------------------
KOKORO_VOICES: dict[str, str] = {
    # Inglês Americano — Feminino
    "af_heart": "Americana Feminina — heart (padrão, natural)",
    "af_bella": "Americana Feminina — bella (suave)",
    "af_sarah": "Americana Feminina — sarah (clara)",
    "af_nicole": "Americana Feminina — nicole (expressiva)",
    "af_sky": "Americana Feminina — sky (animada)",
    # Inglês Americano — Masculino
    "am_adam": "Americano Masculino — adam (robusto)",
    "am_michael": "Americano Masculino — michael (neutro)",
    # Inglês Britânico — Feminino
    "bf_emma": "Britânica Feminina — emma (elegante)",
    "bf_isabella": "Britânica Feminina — isabella (refinada)",
    # Inglês Britânico — Masculino
    "bm_george": "Britânico Masculino — george (formal)",
    "bm_lewis": "Britânico Masculino — lewis (conversacional)",
}

DEFAULT_VOICE = "af_heart"
KOKORO_SAMPLE_RATE = 24000  # Hz — saída nativa do Kokoro

# Paths padrão para os arquivos do modelo (baixados pelo usuário ou auto-download)
_MODEL_DIR = Path.home() / ".local" / "share" / "jarvis" / "models" / "kokoro"
DEFAULT_MODEL_PATH = _MODEL_DIR / "kokoro-v1.0.int8.onnx"
DEFAULT_VOICES_PATH = _MODEL_DIR / "voices-v1.0.bin"

# URLs para download automático
_MODEL_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.int8.onnx"
_VOICES_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"


class KokoroTTS:
    """TTS usando Kokoro-82M via ONNX Runtime (CPU only).

    Roda em thread de worker dedicada com queue para não bloquear o pipeline
    principal. Suporta interrupção imediata e múltiplas vozes.

    Parâmetros
    ----------
    voice:
        ID da voz. Ver ``KOKORO_VOICES`` para a lista completa.
    speed:
        Velocidade da fala. 1.0 = normal; 1.2 = 20% mais rápido.
    lang:
        Idioma para phonemização. ``"en-us"`` ou ``"en-gb"``.
    model_path:
        Caminho para o arquivo ONNX. ``None`` usa o caminho padrão com
        auto-download se necessário.
    voices_path:
        Caminho para o arquivo de vozes `.bin`. ``None`` usa o padrão.
    """

    def __init__(
        self,
        voice: str = DEFAULT_VOICE,
        speed: float = 1.0,
        lang: str = "en-us",
        model_path: Optional[str] = None,
        voices_path: Optional[str] = None,
    ) -> None:
        self.voice = voice
        self.speed = speed
        self.lang = lang
        self._model_path = Path(model_path) if model_path else DEFAULT_MODEL_PATH
        self._voices_path = Path(voices_path) if voices_path else DEFAULT_VOICES_PATH

        self._kokoro = None
        self._loaded = False
        self._init_lock = threading.Lock()
        self._init_error: Optional[str] = None

        # Fila e worker thread para síntese assíncrona
        self._queue: queue.Queue[Optional[Tuple[str, Optional[Callable[[], None]]]]] = queue.Queue()
        self._worker: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._interrupt_event = threading.Event()
        self._is_speaking = threading.Event()

    # ------------------------------------------------------------------
    # Gerenciamento de ciclo de vida
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Inicia a thread worker e pré-carrega o modelo."""
        if self._worker is not None:
            return
        self._stop_event.clear()
        # Pré-carregar em background para não bloquear o caller
        threading.Thread(target=self._ensure_loaded, daemon=True, name="kokoro-init").start()
        self._worker = threading.Thread(target=self._run, daemon=True, name="kokoro-worker")
        self._worker.start()
        logger.info("🔊 KokoroTTS worker iniciado.")

    def stop(self) -> None:
        """Para o worker e libera recursos."""
        self.interrupt()
        self._stop_event.set()
        self._queue.put_nowait(None)  # sentinela para desbloquear a fila
        if self._worker is not None:
            self._worker.join(timeout=3.0)
            self._worker = None
        logger.info("KokoroTTS worker parado.")

    def speak(
        self,
        text: str,
        on_done: Optional[Callable[[], None]] = None,
    ) -> None:
        """Enfileira texto para síntese e reprodução.

        Parâmetros
        ----------
        text:
            Texto a ser sintetizado.
        on_done:
            Callback chamado quando a reprodução terminar (sem interrupção).
        """
        if not text.strip():
            return
        if self._worker is None:
            self.start()
        self._interrupt_event.clear()
        self._queue.put_nowait((text, on_done))

    def interrupt(self) -> None:
        """Interrompe a reprodução atual imediatamente."""
        self._interrupt_event.set()

    @property
    def is_speaking(self) -> bool:
        return self._is_speaking.is_set()

    def set_voice(self, voice: str) -> None:
        """Troca a voz em tempo de execução."""
        if voice not in KOKORO_VOICES:
            logger.warning("Voz '%s' não reconhecida. Usando '%s'.", voice, self.voice)
            return
        self.voice = voice
        logger.info("Voz alterada para: %s", voice)

    def available_voices(self) -> dict[str, str]:
        return dict(KOKORO_VOICES)

    # ------------------------------------------------------------------
    # Inicialização com auto-download
    # ------------------------------------------------------------------

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        with self._init_lock:
            if self._loaded:
                return
            try:
                from kokoro_onnx import Kokoro  # type: ignore[import]
            except ImportError as exc:
                self._init_error = str(exc)
                raise RuntimeError(
                    "kokoro-onnx não instalado. Execute: pip install kokoro-onnx"
                ) from exc

            # Auto-download se necessário
            _MODEL_DIR.mkdir(parents=True, exist_ok=True)
            _maybe_download(self._model_path, _MODEL_URL, "kokoro ONNX model")
            _maybe_download(self._voices_path, _VOICES_URL, "kokoro voices")

            logger.info("⏳ Carregando Kokoro-82M ONNX (CPU)…")
            self._kokoro = Kokoro(str(self._model_path), str(self._voices_path))
            logger.info("✅ Kokoro-82M carregado — %d vozes disponíveis.", len(KOKORO_VOICES))
            self._loaded = True

    # ------------------------------------------------------------------
    # Worker loop
    # ------------------------------------------------------------------

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                item = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if item is None:  # sentinela de parada
                break
            text, on_done = item
            try:
                self._synthesize_and_play(text, on_done)
            except Exception as exc:
                logger.error("Erro na síntese Kokoro: %s", exc, exc_info=True)

    def _synthesize_and_play(
        self,
        text: str,
        on_done: Optional[Callable[[], None]],
    ) -> None:
        """Sintetiza e reproduz um trecho de texto."""
        try:
            self._ensure_loaded()
        except RuntimeError as exc:
            logger.error("KokoroTTS não disponível: %s", exc)
            return

        assert self._kokoro is not None
        self._is_speaking.set()
        interrupted = False

        try:
            logger.debug("🔊 Sintetizando: %.60s…", text)
            samples, sample_rate = self._kokoro.create(
                text,
                voice=self.voice,
                speed=self.speed,
                lang=self.lang,
            )
            # samples é float32 numpy array, sample_rate é int (normalmente 24000)
            _play_blocking(samples, sample_rate, self._interrupt_event)
            interrupted = self._interrupt_event.is_set()
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


def _play_blocking(
    samples: np.ndarray,
    sample_rate: int,
    interrupt_event: threading.Event,
) -> None:
    """Reproduz áudio PCM float32 bloqueando até o fim ou interrupção."""
    try:
        import sounddevice as sd  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError(
            "sounddevice não instalado. Execute: pip install sounddevice"
        ) from exc

    # Garantir float32 mono
    audio = np.asarray(samples, dtype=np.float32)
    if audio.ndim == 2:
        audio = audio.mean(axis=1)

    # Reprodução em blocos para permitir interrupção responsiva
    block_size = int(sample_rate * 0.05)  # blocos de 50 ms
    stream = sd.OutputStream(
        samplerate=sample_rate,
        channels=1,
        dtype="float32",
        blocksize=block_size,
    )
    with stream:
        offset = 0
        while offset < len(audio):
            if interrupt_event.is_set():
                break
            chunk = audio[offset : offset + block_size]
            stream.write(chunk)
            offset += block_size


def _maybe_download(path: Path, url: str, label: str) -> None:
    """Baixa um arquivo se ainda não existir."""
    if path.exists():
        return
    logger.info("📥 Baixando %s de %s…", label, url)
    try:
        import requests  # type: ignore[import]

        with requests.get(url, stream=True, timeout=120) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0))
            downloaded = 0
            tmp = path.with_suffix(".tmp")
            with open(tmp, "wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        pct = downloaded / total * 100
                        if downloaded % (1024 * 1024 * 5) < 65536:
                            logger.info("  %s: %.0f%%", label, pct)
            tmp.rename(path)
        logger.info("✅ %s baixado: %s", label, path.name)
    except Exception as exc:
        logger.error("❌ Falha ao baixar %s: %s", label, exc)
        raise
