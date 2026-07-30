"""Wrapper para Faster-Whisper STT rodando 100% em CPU via CTranslate2."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# Modelos disponíveis em ordem crescente de tamanho/qualidade
WHISPER_MODELS = ("tiny", "base", "small", "medium", "large-v2", "large-v3")


class WhisperSTT:
    """Speech-to-Text usando Faster-Whisper com CTranslate2 (CPU).

    Parâmetros
    ----------
    model_size:
        Tamanho do modelo Whisper. ``"small"`` é o melhor custo-benefício para CPU.
    device:
        ``"cpu"`` — sem consumo de GPU. CTranslate2 é extremamente eficiente em CPU.
    compute_type:
        ``"int8"`` para máxima performance em CPU; ``"float32"`` para máxima precisão.
    language:
        Idioma fixo (ex.: ``"pt"``). ``None`` activa detecção automática.
    """

    def __init__(
        self,
        model_size: str = "small",
        device: str = "cpu",
        compute_type: str = "int8",
        language: Optional[str] = None,
        download_root: Optional[str] = None,
    ) -> None:
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.language = language
        self.download_root = download_root or str(
            Path.home() / ".local" / "share" / "jarvis" / "models" / "whisper"
        )

        self._model = None
        self._loaded = False

    # ------------------------------------------------------------------
    # Inicialização lazy — não carrega o modelo até a primeira transcrição
    # ------------------------------------------------------------------

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        try:
            from faster_whisper import WhisperModel  # type: ignore[import]
        except ImportError as exc:
            raise RuntimeError(
                "faster-whisper não instalado. Execute: pip install faster-whisper"
            ) from exc

        os.makedirs(self.download_root, exist_ok=True)
        logger.info(
            "⏳ Carregando Faster-Whisper '%s' em %s (%s)…",
            self.model_size,
            self.device.upper(),
            self.compute_type,
        )
        self._model = WhisperModel(
            self.model_size,
            device=self.device,
            compute_type=self.compute_type,
            download_root=self.download_root,
        )
        logger.info("✅ Faster-Whisper carregado.")
        self._loaded = True

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def transcribe(
        self,
        audio: np.ndarray,
        sample_rate: int = 16000,
        *,
        beam_size: int = 5,
        vad_filter: bool = True,
        vad_threshold: float = 0.5,
        min_silence_duration_ms: int = 300,
        no_speech_threshold: float = 0.6,
        condition_on_previous_text: bool = False,
    ) -> str:
        """Transcreve áudio PCM float32 mono.

        Parâmetros
        ----------
        audio:
            Array numpy float32 com valores normalizados em [-1, 1].
        sample_rate:
            Taxa de amostragem do áudio. O Whisper requer 16 kHz internamente;
            a conversão é feita automaticamente se necessário.

        Retorna
        -------
        str
            Texto transcrito (vazio se nenhuma fala detectada).
        """
        self._ensure_loaded()
        assert self._model is not None

        # Normalizar para float32 se necessário
        if audio.dtype != np.float32:
            audio = audio.astype(np.float32)

        # Resample para 16 kHz se necessário (faster-whisper aceita qualquer SR)
        if sample_rate != 16000:
            audio = _resample(audio, sample_rate, 16000)

        segments, info = self._model.transcribe(
            audio,
            language=self.language,
            beam_size=beam_size,
            vad_filter=vad_filter,
            vad_parameters={
                "threshold": vad_threshold,
                "min_silence_duration_ms": min_silence_duration_ms,
            },
            no_speech_threshold=no_speech_threshold,
            condition_on_previous_text=condition_on_previous_text,
        )

        text = " ".join(seg.text.strip() for seg in segments).strip()
        if text:
            logger.debug("🎙️ Transcrito [lang=%s, prob=%.2f]: %s", info.language, info.language_probability, text)
        return text

    def transcribe_file(self, path: str | Path, **kwargs: object) -> str:
        """Transcreve um arquivo de áudio (WAV/MP3/etc.)."""
        self._ensure_loaded()
        assert self._model is not None

        segments, _ = self._model.transcribe(str(path), **kwargs)  # type: ignore[arg-type]
        return " ".join(seg.text.strip() for seg in segments).strip()

    def unload(self) -> None:
        """Libera o modelo da memória."""
        self._model = None
        self._loaded = False
        logger.info("Faster-Whisper descarregado.")


# ---------------------------------------------------------------------------
# Utilitários internos
# ---------------------------------------------------------------------------


def _resample(audio: np.ndarray, orig_sr: int, target_sr: int) -> np.ndarray:
    """Resample simples via interpolação linear (sem dependência extra)."""
    if orig_sr == target_sr:
        return audio
    duration = len(audio) / orig_sr
    target_len = int(duration * target_sr)
    return np.interp(
        np.linspace(0, len(audio) - 1, target_len),
        np.arange(len(audio)),
        audio,
    ).astype(np.float32)
