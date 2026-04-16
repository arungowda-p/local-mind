# LocalMind

Lightweight, **fully offline** AI assistant that runs on your local machine. Chat with a quantized LLM, teach it new knowledge from URLs, and use voice input — no cloud APIs required.

## Features

- **Local LLM inference** — runs GGUF models via `llama-cpp-python` (CPU or GPU offload). Ships with presets for TinyLlama 1.1B, Phi-3-mini 4K, and Qwen2.5-1.5B.
- **RAG learning** — paste a URL; the content is extracted, chunked, embedded with `all-MiniLM-L6-v2`, and stored in ChromaDB. The chat engine retrieves relevant context before answering.
- **Voice input** — browser mic → `faster-whisper` (tiny/base, int8 on CPU). Fully offline transcription.
- **Code generation & execution** — ask LocalMind to write code; it responds with fenced code blocks. Click **Run** in the UI to execute Python, JavaScript, TypeScript, Shell, or PowerShell in a sandboxed subprocess — all offline, with timeout protection and output capture.
- **Neural decision engine** — 3-stage pipeline (intent classifier, confidence scorer, action selector) built on the same embeddings. Routes queries intelligently: auto-learns URLs, generates code, executes scripts, gates low-confidence answers, picks RAG vs. direct chat vs. summarize — adds < 1 MB RAM, < 5 ms per query.
- **Streaming chat** — Server-Sent Events stream tokens + live decision metadata to the React UI.
- **Offline-first** — every model runs locally; the UI ships a PWA manifest.

## Requirements

- **Python 3.11+**
- **Node.js 20+** (only for building the UI)
- ~2-4 GB disk per model (downloaded on first load)
- 8 GB RAM minimum (TinyLlama); 16 GB recommended (Phi-3)

## Setup

```bash
cd local-mind
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -e .

# Optional: voice support
pip install -e ".[voice]"
```

### Build the web UI

```bash
cd web-ui
npm ci
npm run build
```

### Pre-download a model (optional)

```bash
localmind download tinyllama-1.1b
# or
localmind download phi-3-mini
localmind download qwen2.5-1.5b
```

Models are cached in `data/models/`.

## Usage

### Start the server

```bash
localmind serve
# or with custom host/port:
localmind serve --host 0.0.0.0 --port 9000
```

Open **http://127.0.0.1:8766**.

### Workflow

1. **Open the sidebar** (hamburger menu on mobile, always visible on desktop).
2. **Load a model** — click *Download* (first time) or *Load* next to a model name.
3. **Teach it** — paste a URL under *Learn from URL* and click *Add*. Repeat for multiple sources.
4. **Chat** — type or use the mic button. The assistant automatically retrieves relevant learned content.
5. **New chat** — click *New chat* to clear history (knowledge persists).

### UI development (hot reload)

Terminal 1:

```bash
localmind serve
```

Terminal 2:

```bash
cd web-ui
npm run dev
```

Open **http://127.0.0.1:5174** (Vite proxies `/api` to the Python server).

## API reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/health` | Server + model status |
| GET | `/api/models` | List available models |
| POST | `/api/models/load` | `{ "name": "tinyllama-1.1b" }` |
| POST | `/api/models/unload` | Free model memory |
| POST | `/api/chat` | `{ "message": "…", "stream": true, "smart": true }` — SSE |
| POST | `/api/decide` | `{ "message": "…" }` — intent + confidence + action (no LLM) |
| POST | `/api/learn/url` | `{ "url": "https://…" }` |
| POST | `/api/learn/text` | `{ "text": "…", "source": "notes" }` |
| GET | `/api/knowledge/stats` | Chunk count |
| POST | `/api/knowledge/clear` | Wipe knowledge base |
| POST | `/api/code/run` | `{ "code": "…", "language": "python", "timeout": 30 }` |
| GET | `/api/code/runtimes` | Detected runtimes (Python, Node, etc.) |
| POST | `/api/voice/transcribe` | Multipart audio file upload |

## Project structure

```
local-mind/
├── src/local_mind/
│   ├── cli.py          — CLI entry (serve / download / models)
│   ├── config.py       — Settings (dirs, model params, server)
│   ├── models.py       — GGUF model download + llama-cpp lifecycle
│   ├── knowledge.py    — URL fetch, chunk, embed, ChromaDB store + query
│   ├── chat.py         — RAG prompt assembly + decision-aware streaming
│   ├── code_exec.py    — Sandboxed subprocess runner (Python/JS/TS/Shell/PS)
│   ├── decision.py     — Neural decision engine (intent, confidence, action)
│   ├── voice.py        — faster-whisper transcription (optional)
│   ├── server.py       — FastAPI app (all API routes + static UI)
│   └── ui_dist/        — Vite build output (generated)
├── web-ui/
│   └── src/
│       ├── App.tsx
│       ├── api.ts          — fetch wrappers
│       ├── types.ts
│       ├── hooks/
│       │   ├── useChat.ts  — SSE streaming + message state
│       │   └── useVoice.ts — MediaRecorder → transcribe
│       └── components/
│           ├── Sidebar.tsx    — model selector, learn URL, knowledge stats
│           ├── TopBar.tsx     — current model, new chat
│           ├── ChatWindow.tsx — scrolling message list
│           ├── ChatBubble.tsx    — single message with code blocks + decisions
│           ├── CodeBlock.tsx     — syntax display, Run button, output panel
│           ├── DecisionBadge.tsx — intent / action / confidence display
│           ├── ChatInput.tsx    — text + send + stop
│           └── VoiceButton.tsx
├── data/               — (gitignored) models, chroma DB, whisper cache
└── pyproject.toml
```

## Resource usage tips

| Model | RAM (approx) | Speed (CPU) |
|-------|-------------|-------------|
| TinyLlama 1.1B Q4 | ~2 GB | ~15 tok/s |
| Qwen2.5 1.5B Q4 | ~2.5 GB | ~12 tok/s |
| Phi-3-mini 4K Q4 | ~3.5 GB | ~8 tok/s |

Set `LOCALMIND_DATA` env var to change where models and DB are stored (default: `./data`).

## Decision engine

Every chat message passes through a 3-stage neural pipeline before the LLM is invoked:

### 1. Intent classifier

Classifies the user message into one of: `question`, `action`, `learn`, `chitchat`, `clarify`, `code`. Uses cosine similarity against centroid embeddings built from seed examples. Shown as a colored badge on each assistant reply.

### 2. Confidence scorer

Measures how well the knowledge base covers the query. Combines top similarity, average similarity, and spread into a 0-1 score. Determines whether to add a low-confidence caveat to the system prompt.

| Confidence | Behavior |
|-----------|----------|
| >= 75% | Use RAG context directly |
| 45-75% | Use RAG but add transparency caveat |
| < 45% | Fall back to general knowledge, inform user |

### 3. Action selector

Picks what LocalMind should do: `rag_chat`, `direct_chat`, `learn_url`, `summarize`, `clarify`, `write_code`, or `run_code`. Combines neural similarity to action anchors with intent/confidence heuristic boosts.

- **write_code** — uses a code-generation system prompt; the LLM returns fenced code blocks. The UI renders each block with a **Run** button.
- **run_code** — when the user's message contains a code fence plus keywords like "run" or "execute", the code is executed directly in a sandboxed subprocess (Python, Node.js, Shell, or PowerShell). No LLM call is needed.
- **learn_url** — auto-triggered when a URL is detected and intent is "learn"; the URL is fetched, chunked, embedded, and stored without invoking the LLM.
- **clarify** — responds with a clarification request instead of hallucinating.
- **summarize** — uses a summarization-focused system prompt.

All three stages reuse the same `all-MiniLM-L6-v2` embeddings already loaded for RAG, adding < 1 MB of centroid vectors and < 5 ms per query.

The `/api/decide` endpoint runs the pipeline standalone (no model required) for testing or external integrations. Pass `"smart": false` to `/api/chat` to bypass the decision engine entirely.

## License

MIT
