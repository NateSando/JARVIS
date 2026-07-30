"""Orquestrador do pipeline de voz: Microfone → STT → LLM → TTS."""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

import numpy as np

from .audio_io import AudioCapture
from .stt import WhisperSTT
from .tts_kokoro import KokoroTTS, KOKORO_VOICES

logger = logging.getLogger(__name__)


class VoicePipeline:
    """Pipeline completo de voz para o JARVIS.

    Fluxo:
        Microfone → Faster-Whisper (STT, CPU)
                  → LLM via Ollama (call_llm_direct)
                  → Kokoro-ONNX (TTS, CPU)
                  → Saída de áudio

    Parâmetros
    ----------
    base_url:
        URL base do Ollama (ex.: ``"http://127.0.0.1:11434"``).
    chat_model:
        Modelo LLM (ex.: ``"gemma:2b"``).
    system_prompt:
        Prompt de sistema enviado à LLM.
    whisper_model_size:
        Tamanho do modelo Whisper (``"tiny"``, ``"base"``, ``"small"``, ``"medium"``).
    kokoro_voice:
        ID da voz Kokoro. Ver ``KOKORO_VOICES``.
    kokoro_speed:
        Velocidade de fala (1.0 = normal).
    whisper_language:
        Idioma fixo para o Whisper (``None`` = auto-detecção).
    on_transcribed:
        Callback chamado com o texto transcrito (para log / UI).
    on_response:
        Callback chamado com a resposta da LLM (para log / UI).
    llm_timeout_sec:
        Timeout para chamada à LLM.
    silence_threshold:
        Limiar de energia RMS para detecção de fala no microfone.
    silence_duration_s:
        Duração de silêncio (segundos) para considerar fim da fala.
    audio_device:
        Dispositivo de entrada (``None`` = padrão do sistema).
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434",
        chat_model: str = "gemma:2b",
        system_prompt: str = "Você é JARVIS, um assistente de voz conciso e útil. Responda em poucas frases.",
        whisper_model_size: str = "small",
        kokoro_voice: str = "af_heart",
        kokoro_speed: float = 1.0,
        whisper_language: Optional[str] = None,
        on_transcribed: Optional[Callable[[str], None]] = None,
        on_response: Optional[Callable[[str], None]] = None,
        llm_timeout_sec: float = 60.0,
        silence_threshold: float = 0.015,
        silence_duration_s: float = 1.2,
        audio_device: Optional[str | int] = None,
    ) -> None:
        self.base_url = base_url
        self.chat_model = chat_model
        self.system_prompt = system_prompt
        self.llm_timeout_sec = llm_timeout_sec
        self.on_transcribed = on_transcribed
        self.on_response = on_response

        # STT
        self._stt = WhisperSTT(
            model_size=whisper_model_size,
            device="cpu",
            compute_type="int8",
            language=whisper_language,
        )

        # TTS
        self._tts = KokoroTTS(
            voice=kokoro_voice,
            speed=kokoro_speed,
        )

        # Captura de microfone
        self._capture = AudioCapture(
            on_speech=self._on_audio,
            silence_threshold=silence_threshold,
            silence_duration_s=silence_duration_s,
            device=audio_device,
        )

        self._running = False
        self._processing_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Inicia o pipeline (microfone + TTS worker)."""
        if self._running:
            return
        self._running = True
        self._tts.start()
        self._capture.start()
        logger.info("🚀 VoicePipeline iniciado. Fale agora…")

    def stop(self) -> None:
        """Para o pipeline de forma limpa."""
        if not self._running:
            return
        self._running = False
        self._capture.stop()
        self._tts.stop()
        logger.info("🛑 VoicePipeline parado.")

    def interrupt_tts(self) -> None:
        """Interrompe a fala atual imediatamente."""
        self._tts.interrupt()

    def set_voice(self, voice: str) -> None:
        """Troca a voz do Kokoro em tempo de execução."""
        self._tts.set_voice(voice)

    @property
    def available_voices(self) -> dict[str, str]:
        return KOKORO_VOICES

    # ------------------------------------------------------------------
    # Callback interno: áudio capturado → STT → LLM → TTS
    # ------------------------------------------------------------------

    def _on_audio(self, audio: np.ndarray) -> None:
        """Processado em thread separada a cada fala detectada."""
        if not self._running:
            return

        # Serializar processamentos para evitar interleaving de respostas
        with self._processing_lock:
            # 1. STT
            logger.info("🎙️ Transcrevendo…")
            text = self._stt.transcribe(audio)

            if not text:
                logger.debug("Nenhuma fala detectada após transcrição.")
                return

            logger.info("📝 Transcrito: %s", text)
            if self._tts.is_speaking:
                self._tts.interrupt()

            if self.on_transcribed:
                try:
                    self.on_transcribed(text)
                except Exception:
                    pass

            # 2. LLM
            logger.info("🤖 Consultando LLM '%s'…", self.chat_model)
            response = self._call_llm(text)

            if not response:
                logger.warning("LLM retornou resposta vazia.")
                return

            logger.info("💬 Resposta: %s", response)
            if self.on_response:
                try:
                    self.on_response(response)
                except Exception:
                    pass

            # 3. TTS
            self._tts.speak(response)

    def _call_llm(self, user_text: str) -> Optional[str]:
        """Chama a LLM via Ollama e retorna a resposta."""
        try:
            from ..llm import call_llm_direct  # type: ignore[import]

            return call_llm_direct(
                base_url=self.base_url,
                chat_model=self.chat_model,
                system_prompt=self.system_prompt,
                user_content=user_text,
                timeout_sec=self.llm_timeout_sec,
            )
        except Exception as exc:
            logger.error("Erro ao chamar LLM: %s", exc, exc_info=True)
            return None
