# Mudanças de Arquitetura: Voz J.A.R.V.I.S., Cérebro Obsidian e Acesso a Código/Sistema

## 1. Resumo Executivo

Três capacidades foram adicionadas ao JARVIS:

1. **Voz J.A.R.V.I.S. (Kokoro)**: TTS multilíngue em CPU, voz masculina (mistura `pm_alex` + `bm_george`) com coloração robótica ajustável e síntese em streaming (a fala começa após a primeira frase). É o único motor de voz (Piper e Chatterbox foram removidos).
2. **Cérebro Obsidian**: tudo o que o JARVIS aprende vira notas Markdown ligadas num cofre Obsidian, junto com um mapa de habilidades gerado a partir do prompt mestre. A página "🧠 Cérebro" mostra o cofre como uma esfera viva no estilo do graph view do Obsidian; nomes aparecem só no hover (o nó e as ramificações ligadas a ele). As notas do usuário também entram na memória das respostas.
3. **Acesso a código e sistema (opt-in, somente leitura)**: as ferramentas `codeInspect` e `systemInfo` permitem ao JARVIS entender a estrutura dos projetos do usuário e o estado da máquina, apenas depois de permissão explícita.

Problema resolvido: o assistente não tinha voz em português com a persona pedida, não tinha uma representação navegável do conhecimento e não podia responder sobre o código e o computador do usuário.

## 2. Arquitetura e Fluxo de Dados

### 2.1 Visão geral

```mermaid
flowchart LR
    U((Usuário)) -- voz --> L[Listener + Whisper]
    L --> R[Reply engine<br/>LLM local via Ollama]
    R -- texto --> K[KokoroTTSEngine]
    K --> S[KokoroSynth<br/>voz + blend] --> FX[voice_fx<br/>efeito robótico] --> AUD[(Alto-falante)]

    R -- enrichment --> G[(Knowledge graph<br/>SQLite)]
    R -- enrichment --> V[(Cofre Obsidian)]
    G -- mutation listener --> BS[BrainSyncer<br/>debounce 2 s] --> V
    P[Prompt mestre] -- seção ###--> BS

    R -- tool call --> CI[codeInspect]
    R -- tool call --> SI[systemInfo]
    CI --> POL{AccessPolicy<br/>pastas concedidas}
    POL --> CODE[(Código do usuário)]
    SI --> OS[(psutil / SO)]

    V --> MV[Brain server<br/>/api/cerebro] --> UI[Janela Cérebro<br/>canvas offline]
```

### 2.2 Voz (streaming)

```mermaid
sequenceDiagram
    participant E as Reply engine
    participant T as KokoroTTSEngine
    participant P as Thread produtora
    participant A as Áudio
    E->>T: speak(texto, on_done, on_duration)
    T->>T: split_for_speech (1º trecho curto)
    T->>P: inicia síntese
    loop cada trecho
        P->>P: KokoroSynth.synthesize + voice_fx
        P-->>T: amostras (fila maxsize=2)
        P-->>E: duração acumulada (eco)
        T->>A: reproduz (interrompível a cada 50 ms)
    end
    T-->>E: on_done (se não interrompido)
```

### 2.3 Cérebro

- `brain/vault.py` lê o cofre (wikilinks, tags, frontmatter, pontos) com cache por arquivo (mtime + tamanho).
- `brain/sync.py` semeia `JARVIS/Habilidades/` (uma vez; depois a nota é do usuário), espelha o grafo em `JARVIS/Memória/` e regenera `JARVIS/Cérebro.md`. Só reescreve arquivos com `jarvis_id`.
- `brain/brain_map.py` produz nós, links e áreas para a página; links sem nota viram nós "conceito" (como no Obsidian).
- A página usa física própria: disco único de partida + gravidade central + repulsão local em grade espacial + molas nos links, resultando numa esfera redonda. Cada quadro é ~O(n); grafos grandes param de redesenhar quando estabilizam.

### 2.4 Acesso a código e sistema

```mermaid
flowchart TD
    A[LLM pede codeInspect path=X] --> B{code_access_roots vazio?}
    B -- sim --> R1[Recusa + como conceder]
    B -- não --> C[resolve: expande ~, relativo à raiz,<br/>segue symlinks, colapsa ..]
    C --> D{dentro de uma raiz?}
    D -- não --> R2[Acesso negado]
    D -- sim --> E{arquivo de segredo?<br/>.env, chaves, credenciais}
    E -- sim --> R2
    E -- não --> F[operação com limites<br/>overview/tree/search/read/outline]
    F --> G[scrub_secrets] --> H[texto bruto para o LLM]
```

## 3. Arquivos Criados/Modificados

| Arquivo | Motivo |
|---------|--------|
| `src/jarvis/output/voice_fx.py` | Efeito robótico em NumPy vetorizado (comb metálico, doubler, ring mod leve, casamento de loudness, proteção de pico) |
| `src/jarvis/voice/tts_kokoro.py` | `KokoroSynth` compartilhado (download, blend de vozes, síntese), `split_for_speech`, `parse_voice_spec` |
| `src/jarvis/output/tts_kokoro.py` | Motor do daemon com streaming produtor/consumidor |
| `src/jarvis/output/tts.py`, `src/jarvis/daemon.py` | Fábrica e inicialização do motor `kokoro` |
| `src/jarvis/config.py` | `kokoro_*`, `obsidian_*`, `code_access_roots`, `system_access_enabled` |
| `src/jarvis/voice/cli.py`, `pipeline.py` | CLI de voz usa o prompt mestre e a voz do config |
| `src/jarvis/brain/*` | Cofre, sincronização, mapa do cérebro, recall do cofre, spec |
| `src/jarvis/reply/engine.py` | Notas do cofre entram junto com o grafo no enrichment |
| `src/desktop_app/brain_page.py` | Página do cérebro (canvas offline) |
| `src/desktop_app/brain_server.py`, `app.py` | Servidor local de `/cerebro` e `/api/cerebro`; janela do Cérebro aberta ao iniciar |
| `src/desktop_app/themes.py` | Cores `graph_*` do cérebro |
| `src/desktop_app/settings_window.py` | Seções Kokoro, Obsidian e "Code & System Access" |
| `src/jarvis/tools/builtin/code_access/*` | `AccessPolicy`, varredura, `codeInspect`, `systemInfo`, spec |
| `src/jarvis/tools/registry.py` | Registro das novas ferramentas |
| `jarvis_desktop.spec` | Empacotamento do Kokoro/espeak-ng e dos novos módulos |
| `README.md`, `CLAUDE.md`, `docs/llm_contexts.md`, specs | Documentação |
| `tests/...`, `evals/test_tool_selection.py` | Testes de comportamento e casos de eval de roteamento |

## 4. Decisões de Engenharia e Segurança (ADR)

**ADR-1: Kokoro ONNX em CPU como padrão.** Multilíngue (pt-br), sem PyTorch, ~120 MB. Alternativas descartadas e depois removidas: Piper (só inglês), Chatterbox (GPU/PyTorch pesado). Trade-off: síntese mais lenta que Piper, mitigada com streaming por frases.

**ADR-2: Blend de vetores de estilo em vez de outro modelo.** Pronúncia vem do idioma (`kokoro_lang`), timbre do blend. Permite "sotaque de mordomo" sem trocar de modelo nem custo extra de inferência.

**ADR-3: O cofre Obsidian é a única representação em disco do cérebro.** A página, o recall e o Obsidian leem os mesmos arquivos; não há banco paralelo para divergir. Arquivos do usuário nunca são alterados (só os com `jarvis_id`).

**ADR-4: Canvas próprio em vez de d3/CDN.** Funciona offline (privacidade em primeiro lugar) e permite otimizações específicas (lotes por cor, grade espacial, parada de redesenho quando ocioso).

**ADR-5: Acesso somente leitura, opt-in, por allowlist de pastas.** Seguindo zero-trust para agentes: nenhuma execução de shell, escrita ou exclusão. Caminhos são resolvidos (symlinks e `..`) antes da checagem, bloqueando traversal e escapes. Arquivos de segredo são recusados por nome e o conteúdo lido passa por `scrub_secrets`. `systemInfo` omite linhas de comando, variáveis de ambiente, usuário e rede. Trade-off: o JARVIS não corrige código sozinho; ele aponta onde e como, e o usuário aplica.

**ADR-6: Ferramentas registradas mesmo sem permissão.** Assim o roteador ainda as escolhe e o JARVIS pode dizer exatamente qual configuração liberar, em vez de alucinar uma resposta.

**ADR-7: Recall do cofre sem nova chamada de LLM.** Busca lexical com folding Unicode (independente de idioma) e o mesmo gate do grafo; os resultados passam pelo digest existente. Sem custo extra de latência.

## 4b. Enxugamento: só o que serve ao cérebro e à conversa

Por decisão do usuário, o projeto foi reduzido ao essencial: voz Kokoro, conversa, memória, Cérebro Obsidian e acesso a código/sistema.

```mermaid
flowchart LR
    L[Rodar o app] --> T[Ícone na bandeja]
    T --> B[Janela do Cérebro abre]
    T --> O[OllamaBootstrap em segundo plano]
    O -->|CLI ausente| N[Aviso na bandeja]
    O -->|servidor parado| S[Inicia o Ollama]
    O -->|modelos faltando| P[ollama pull com progresso]
    S --> P --> D[Daemon de voz começa a ouvir]
    O -->|tudo ok| D
```

| Removido | Motivo |
|----------|--------|
| Splash, assistente de configuração, aviso de modelo não oficial, diálogo de crash, janela de logs, janela do Rosto, diálogo de diário | "Telas antes de entrar"; substituídas por `ollama_setup.py` silencioso + notificações |
| Visualizador de memória (diário, refeições, explorador do grafo) | O Cérebro substitui a visualização; manutenções seguem como funções Python |
| Auto-updater | Baixava builds do repositório original e poderia sobrescrever esta versão |
| Ditado por atalho | Fora do escopo pedido |
| Ferramentas de refeições (logMeal, fetchMeals, deleteMeal) | Fora do escopo pedido |
| Integrações MCP | Fora do escopo; a falha de import do `mcp` com pydantic 2.14 derrubava o app |
| Piper e Chatterbox | Kokoro é a única voz; a regra "responda em inglês" saiu junto |

**ADR-8: inicialização sem telas.** Tudo que precisava de ação do usuário vira notificação na bandeja + linha no terminal. O único caso que impede o funcionamento (Ollama não instalado) não abre janela: o Cérebro abre mesmo assim e a bandeja explica como instalar.

**ADR-9: núcleo sem ganchos de UI.** As chamadas do núcleo (`listener`, `state_manager`, `engine`, `tts_kokoro`) ao widget do Rosto foram removidas; o núcleo não conhece mais o app desktop.

## 5. Guia de Execução e Validação

Não há variáveis de ambiente novas nem segredos: tudo é configurado em `~/.config/jarvis/config.json` ou na janela de configurações.

```json
{
  "kokoro_voice": "pm_alex:0.7,bm_george:0.3",
  "kokoro_lang": "pt-br",
  "kokoro_robot_effect": 0.35,
  "obsidian_enabled": true,
  "obsidian_vault_path": "~/Obsidian/JARVIS",
  "code_access_roots": ["~/projetos/lexguard"],
  "system_access_enabled": true
}
```

Testes:

```bash
python -m pytest tests/test_voice_fx.py tests/test_kokoro_tts.py tests/test_obsidian_brain.py \
  tests/test_brain_server.py tests/test_ollama_setup.py tests/test_desktop_app.py \
  tests/tools/builtin/test_code_inspect.py \
  tests/tools/builtin/test_system_info.py tests/test_enrichment.py -q
```

Evals (precisam do Ollama rodando):

```bash
python -m pytest evals/test_tool_selection.py -v
```

Validação manual:
1. 🔊 `cd src && python -m jarvis.voice.cli --list-voices` e depois `python -m jarvis.voice.cli` para ouvir a voz.
2. 🧠 Rodar `bash scripts/run_desktop_app.sh`: a janela do Cérebro abre sozinha, sem telas intermediárias; passar o mouse num nó mostra o nome dele e das ramificações ligadas.
3. 🟣 Abrir `~/Obsidian/JARVIS` como cofre no Obsidian e conferir o graph view.
4. 🗂️ Conceder uma pasta em "Code & System Access" e perguntar: "Jarvis, como está estruturado o meu projeto?"
5. 🖥️ Ativar "System Information" e perguntar: "Jarvis, o que está consumindo memória?"
