# 📋 Controle de Progresso - Inicialização e Otimização do Jarvis

Este arquivo gerencia as etapas de configuração do ambiente operacional do Jarvis no sistema do Natã.

- [x] **Etapa 1: Atualização do arquivo de configuração**
  - Ajustar `~/.config/jarvis/config.json` para usar o modelo mais leve **`gemma:2b`** (1.7 GB) tanto no chat quanto no intent judge, para evitar estouro da memória RAM do sistema.
  - *Status: Concluído.*

- [x] **Etapa 2: Instalação do modelo Gemma 2B & Embeddings**
  - O modelo **`gemma:2b`** já está instalado no Ollama local do usuário.
  - O modelo **`nomic-embed-text`** já está instalado no Ollama local.
  - *Status: Concluído.*

- [x] **Etapa 3: Instalação das dependências de sistema (APT)**
  - Instalar bibliotecas de áudio, interface e compilação para Linux:
    `sudo apt update && sudo apt install -y build-essential python3-dev portaudio19-dev libasound2-dev libayatana-appindicator3-1 tesseract-ocr`
  - *Status: Concluído.*

- [ ] **Etapa 4: Reinicialização e Validação do Jarvis**
  - Executar novamente `bash scripts/run_desktop_app.sh`.
  - Clicar no botão **"Continue Anyway"** quando o alerta de modelo não oficial aparecer.
  - Testar o comando de voz: *"Jarvis, como está o sistema?"*.
  - *Status: Pendente (Aguardando ação do usuário).*
