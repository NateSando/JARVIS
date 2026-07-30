"""CLI interativo para o modo de voz do JARVIS.

Uso::

    # Modo padrão (voz af_heart, modelo small)
    python -m jarvis.voice.cli

    # Com parâmetros
    python -m jarvis.voice.cli --voice bm_george --whisper-model medium --kokoro-speed 1.1

    # Listar vozes disponíveis
    python -m jarvis.voice.cli --list-voices

    # Listar dispositivos de microfone
    python -m jarvis.voice.cli --list-devices
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time

from .pipeline import VoicePipeline
from .tts_kokoro import KOKORO_VOICES
from .audio_io import list_input_devices


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )
    # Suprimir logs verbosos de bibliotecas externas
    for noisy in ("httpx", "httpcore", "urllib3", "requests"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _print_banner() -> None:
    banner = """
╔══════════════════════════════════════════════════════╗
║           JARVIS — Modo Voz (CPU Only)               ║
║  Faster-Whisper STT  +  Kokoro-82M TTS               ║
╚══════════════════════════════════════════════════════╝
Pressione Ctrl+C para encerrar.
"""
    print(banner, flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="jarvis-voice",
        description="JARVIS modo voz: Faster-Whisper → LLM → Kokoro TTS (100% CPU)",
    )

    # STT
    parser.add_argument(
        "--whisper-model",
        default="small",
        choices=["tiny", "base", "small", "medium", "large-v2", "large-v3"],
        help="Tamanho do modelo Whisper (padrão: small)",
        dest="whisper_model",
    )
    parser.add_argument(
        "--language",
        default=None,
        help="Idioma fixo para o Whisper (ex.: 'pt', 'en'). Padrão: auto-detecção.",
    )

    # TTS
    parser.add_argument(
        "--voice",
        default="af_heart",
        choices=list(KOKORO_VOICES.keys()),
        help="Voz do Kokoro TTS (padrão: af_heart)",
    )
    parser.add_argument(
        "--kokoro-speed",
        type=float,
        default=1.0,
        help="Velocidade de fala (padrão: 1.0)",
        dest="kokoro_speed",
    )

    # LLM
    parser.add_argument(
        "--ollama-url",
        default="http://127.0.0.1:11434",
        help="URL base do Ollama (padrão: http://127.0.0.1:11434)",
        dest="ollama_url",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Modelo LLM Ollama. Se omitido, lê do config.json.",
    )
    parser.add_argument(
        "--system-prompt",
        default=None,
        help="Prompt de sistema personalizado.",
        dest="system_prompt",
    )
    parser.add_argument(
        "--llm-timeout",
        type=float,
        default=60.0,
        help="Timeout da chamada LLM em segundos (padrão: 60.0)",
        dest="llm_timeout",
    )

    # Áudio
    parser.add_argument(
        "--silence-threshold",
        type=float,
        default=0.015,
        help="Limiar de energia RMS para detecção de fala (padrão: 0.015)",
        dest="silence_threshold",
    )
    parser.add_argument(
        "--silence-duration",
        type=float,
        default=1.2,
        help="Duração de silêncio para encerrar fala, em segundos (padrão: 1.2)",
        dest="silence_duration",
    )
    parser.add_argument(
        "--device",
        default=None,
        help="Dispositivo de microfone (índice ou nome). Padrão: dispositivo padrão do sistema.",
    )

    # Utilitários
    parser.add_argument(
        "--list-voices",
        action="store_true",
        help="Lista as vozes disponíveis e sai.",
        dest="list_voices",
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="Lista os dispositivos de entrada de áudio e sai.",
        dest="list_devices",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Ativa logs de debug.",
    )

    args = parser.parse_args()
    _setup_logging(args.verbose)

    # ------------------------------------------------------------------
    # Modos informativos
    # ------------------------------------------------------------------

    if args.list_voices:
        print("\n🎙️  Vozes disponíveis no Kokoro-82M:\n")
        for voice_id, desc in KOKORO_VOICES.items():
            print(f"  {voice_id:<20} — {desc}")
        print()
        return 0

    if args.list_devices:
        print("\n🎤  Dispositivos de entrada de áudio:\n")
        devices = list_input_devices()
        if not devices:
            print("  Nenhum dispositivo encontrado.")
        for dev in devices:
            print(f"  [{dev['index']}] {dev['name']} ({dev['channels']} canal(is))")
        print()
        return 0

    # ------------------------------------------------------------------
    # Carregar configuração do JARVIS (se disponível)
    # ------------------------------------------------------------------
    chat_model = args.model
    system_prompt = args.system_prompt

    try:
        from ..config import load_settings  # type: ignore[import]

        settings = load_settings()
        if chat_model is None:
            chat_model = settings.ollama_chat_model
        if system_prompt is None:
            system_prompt = (
                "Você é JARVIS, um assistente de voz conciso e inteligente. "
                "Responda de forma natural, em poucas frases, sem usar markdown. "
                "Seja direto e útil."
            )
        ollama_url = settings.ollama_base_url
    except Exception:
        if chat_model is None:
            chat_model = "gemma:2b"
        if system_prompt is None:
            system_prompt = "Você é JARVIS, um assistente de voz conciso. Responda brevemente."
        ollama_url = args.ollama_url

    _print_banner()
    print(f"  Modelo Whisper : {args.whisper_model} (CPU/CTranslate2)")
    print(f"  Modelo LLM     : {chat_model}")
    print(f"  Voz Kokoro     : {args.voice} — {KOKORO_VOICES.get(args.voice, '?')}")
    print(f"  Velocidade TTS : {args.kokoro_speed}x")
    print(f"  Ollama URL     : {ollama_url}")
    print()

    # ------------------------------------------------------------------
    # Callbacks de UI
    # ------------------------------------------------------------------

    def on_transcribed(text: str) -> None:
        print(f"  🎙️  Você: {text}", flush=True)

    def on_response(text: str) -> None:
        print(f"  🤖  JARVIS: {text}", flush=True)

    # ------------------------------------------------------------------
    # Iniciar pipeline
    # ------------------------------------------------------------------

    pipeline = VoicePipeline(
        base_url=ollama_url,
        chat_model=chat_model,
        system_prompt=system_prompt,
        whisper_model_size=args.whisper_model,
        kokoro_voice=args.voice,
        kokoro_speed=args.kokoro_speed,
        whisper_language=args.language,
        on_transcribed=on_transcribed,
        on_response=on_response,
        llm_timeout_sec=args.llm_timeout,
        silence_threshold=args.silence_threshold,
        silence_duration_s=args.silence_duration,
        audio_device=args.device,
    )

    # Tratamento de Ctrl+C gracioso
    shutdown_event = False

    def _sigint_handler(signum: int, frame: object) -> None:
        nonlocal shutdown_event
        if not shutdown_event:
            print("\n\n⏹️  Encerrando JARVIS Voice…", flush=True)
            shutdown_event = True
            pipeline.stop()

    signal.signal(signal.SIGINT, _sigint_handler)
    signal.signal(signal.SIGTERM, _sigint_handler)

    pipeline.start()
    print("✅ Pipeline ativo. Pode falar!\n", flush=True)

    try:
        while not shutdown_event:
            time.sleep(0.1)
    except KeyboardInterrupt:
        pipeline.stop()

    print("👋 JARVIS Voice encerrado.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
