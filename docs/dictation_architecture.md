# Arquitetura de captura e transcrição de voz (Dictation/Listener)

Este documento descreve, em linguagem técnica, como o Jarvis captura áudio do microfone, processa-o e gera transcrições, incluindo os componentes, dependências e o fluxo de execução.

## Visão geral

- O sistema tem dois caminhos principais que produzem texto a partir de áudio:
  - **DictationEngine**: "hold-to-dictate" (pressione e segure hotkey, solte para transcrever e colar). Implementado em `src/jarvis/dictation/dictation_engine.py`.
  - **VoiceListener**: pipeline contínua usada pelo assistente (wake-word, VAD, intent judge), implementada em `src/jarvis/listening/listener.py`.
- Ambos compartilham um mesmo backend de transcrição (Whisper), com proteção por lock (`transcribe_lock`) para evitar chamadas concorrentes.

## Componentes principais

- Captura de áudio:
  - Biblioteca: `sounddevice` (PortAudio). Usa `InputStream` nativo com sample rate do dispositivo.
  - Resampling: quando necessário, resample linear via `numpy` para a taxa alvo do modelo (ex.: 16000 Hz).
- Ativação:
  - `DictationEngine`: usa `pynput` para escutar hotkey(s) e suportar modo "hands-free" (double-tap).
  - `VoiceListener`: detecta wake words/early-beep e decide se chama o Intent Judge.
- VAD e segmentação:
  - Preferencialmente usa `webrtcvad` quando disponível; caso contrário, usa heurísticas de energia/frames.
- Mitigação de eco:
  - `EchoDetector` com heurísticas para detectar e remover trechos de TTS ecoados antes de aceitar/transcrever voz do usuário.
- Motor ASR (Whisper): duas opções
  - **MLX Whisper** (`mlx_whisper`) — otimizado para Apple Silicon (M1/M2). Chamado quando disponível (path `mlx_whisper.transcribe`).
  - **Faster-Whisper** (`faster_whisper.WhisperModel`) — usa CTranslate2 (CPU/GPU) e é dependência principal para outras plataformas.
- Pós-processamento:
  - Correções por dicionário customizável.
  - Opcional: remoção de "fillers"/limpeza por LLM local (Ollama) via `_llm_clean_dictation` (HTTP para `/api/generate`).
- Integração com UI:
  - Beeps de início/fim gerados em memória; reprodução via `sounddevice` ou APIs nativas.
  - Transcrição é colocada na área de transferência e simulada a tecla de colar (multiplataforma). Histórico salvo e exibido pela UI Qt (`desktop_app/dictation_history.py`).

## Concorrência e segurança de recursos

- `transcribe_lock` (`threading.Lock`) é usado para serializar acessos ao modelo Whisper entre o `DictationEngine` e o `VoiceListener`.
- Transcrições e tarefas longas são executadas em threads de fundo para não bloquear hooks de teclado (evita hangs e crashes do `pynput`).

## Heurísticas de qualidade e rejeição

- Rejeição de "no-speech"/halucinação: função `is_whisper_hallucination` e checagens de `no_speech_prob`.
- Salvamento de fala real quando Whisper mescla TTS-eco + fala do usuário (salvage heuristics) — evita perda de entrada válida.
- Limites: duração máxima de gravação (ex.: 60s para dictation), limite mínimo para transcrição (~0.3s).

## Dependências relevantes (resumido)

- `faster-whisper` (ASR CPU/GPU)
- `mlx-whisper` (option, Apple Silicon)
- `sounddevice` (PortAudio frontend)
- `numpy` (processamento/resample)
- `webrtcvad` (VAD, opcional)
- `pynput` (hotkey / input simulation)
- `ollama` (servidor LLM local, invocado via HTTP)

(Ver `requirements.txt` para versões exatas.)

## Arquivos-chave para inspecionar

- `src/jarvis/dictation/dictation_engine.py` — lógica de hold-to-dictate, gravação, beeps, transcrição e paste.
- `src/jarvis/listening/listener.py` — pipeline contínuo: VAD, echo detection, intent judge e coordenação com modelos.
- `src/desktop_app/setup_wizard.spec.md` — fluxos de instalação/configuração de modelos (Ollama, Whisper) e opções de plataforma.
- `src/desktop_app/dictation_history.py` — UI de histórico de transcrições.
- `requirements.txt` — dependências e notas de plataforma (GPU / MLX etc.).

## Execução local rápida (verificações)

- Verifique dependências e PortAudio

```bash
python -m pip install -r requirements.txt
# No Linux: apt install libportaudio2
```

- Teste captura mínima (informal): executar um REPL Python e abrir um `sounddevice` InputStream para confirmar áudio.

## Observações finais

- A arquitetura privilegia: segurança de recursos (locks), UX responsiva (beeps + threads), e robustez a eco/TTS.
- Plano de otimização óbvia: usar MLX em Apple Silicon para performance, e GPU/CTranslate2 quando disponível no Windows/Linux para throughput maior.

---

Arquivo gerado automaticamente a partir da inspeção do código fonte do projeto.
