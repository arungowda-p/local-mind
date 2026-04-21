from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from local_mind.chat import chat, chat_stream, smart_chat, smart_chat_stream
from local_mind.config import settings
from local_mind.decision import decision_engine
from local_mind.knowledge import knowledge
from local_mind.models import model_manager

log = logging.getLogger(__name__)
app = FastAPI(title="LocalMind", version="0.1.0")


# ── UI dist ──────────────────────────────────────────────────────────────────

def _ui_dist() -> Path:
    env = os.environ.get("LOCALMIND_UI_DIST")
    if env:
        return Path(env).resolve()
    return (Path(__file__).resolve().parent / "ui_dist").resolve()


# ── Pydantic schemas ─────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    history: list[dict[str, str]] = []
    use_rag: bool = True
    temperature: float = 0.7
    max_tokens: int | None = None
    repeat_penalty: float | None = None
    stream: bool = True
    smart: bool = True


class DecideRequest(BaseModel):
    message: str


class CodeRunRequest(BaseModel):
    code: str
    language: str = "python"
    timeout: int = 30
    stdin: str | None = None


class CodeFormatRequest(BaseModel):
    code: str
    language: str = "python"


class LearnUrlRequest(BaseModel):
    url: str


class LearnTextRequest(BaseModel):
    text: str
    source: str = "paste"


class LoadModelRequest(BaseModel):
    name: str


# ── Model endpoints ──────────────────────────────────────────────────────────

@app.get("/api/models")
def list_models() -> list[dict[str, Any]]:
    return model_manager.list_available()


@app.post("/api/models/load")
def load_model(body: LoadModelRequest) -> dict[str, str]:
    try:
        model_manager.load(body.name)
    except FileNotFoundError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, str(e))
    return {"status": "loaded", "model": body.name}


@app.post("/api/models/unload")
def unload_model() -> dict[str, str]:
    model_manager.unload()
    return {"status": "unloaded"}


# ── Chat endpoints ───────────────────────────────────────────────────────────

@app.post("/api/chat")
async def chat_endpoint(body: ChatRequest) -> Any:
    if not model_manager.loaded:
        raise HTTPException(400, "No model loaded. POST /api/models/load first.")

    # Smart mode (with decision engine)
    if body.smart:
        if not body.stream:
            try:
                result = smart_chat(
                    body.message,
                    history=body.history or None,
                    use_rag=body.use_rag,
                    temperature=body.temperature,
                    max_tokens=body.max_tokens,
                    repeat_penalty=body.repeat_penalty,
                )
                return result
            except Exception as e:
                raise HTTPException(500, str(e))

        def smart_sse():
            try:
                for item in smart_chat_stream(
                    body.message,
                    history=body.history or None,
                    use_rag=body.use_rag,
                    temperature=body.temperature,
                    max_tokens=body.max_tokens,
                    repeat_penalty=body.repeat_penalty,
                ):
                    if item["type"] == "decision":
                        yield f"data: {json.dumps({'decision': item['decision']})}\n\n"
                    elif item["type"] == "token":
                        yield f"data: {json.dumps({'token': item['token']})}\n\n"
                    elif item["type"] == "code_results":
                        yield f"data: {json.dumps({'code_results': item['results']})}\n\n"
                    elif item["type"] == "web_sources":
                        yield f"data: {json.dumps({'web_sources': item['web_sources']})}\n\n"
                    elif item["type"] == "rewrite":
                        yield f"data: {json.dumps({'rewrite': item['content']})}\n\n"
                    elif item["type"] == "done":
                        yield "data: [DONE]\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'error': str(e)})}\n\n"

        return StreamingResponse(
            smart_sse(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # Legacy mode (no decision engine)
    if not body.stream:
        try:
            result = chat(
                body.message,
                history=body.history or None,
                use_rag=body.use_rag,
                temperature=body.temperature,
                max_tokens=body.max_tokens,
                repeat_penalty=body.repeat_penalty,
            )
            return result
        except Exception as e:
            raise HTTPException(500, str(e))

    def sse():
        try:
            for token in chat_stream(
                body.message,
                history=body.history or None,
                use_rag=body.use_rag,
                temperature=body.temperature,
                max_tokens=body.max_tokens,
                repeat_penalty=body.repeat_penalty,
            ):
                yield f"data: {json.dumps({'token': token})}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(
        sse(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Decision endpoint ─────────────────────────────────────────────────────────

@app.post("/api/decide")
def decide_endpoint(body: DecideRequest) -> dict[str, Any]:
    try:
        return decision_engine.decide(body.message)
    except Exception as e:
        raise HTTPException(500, str(e))


# ── Code execution ────────────────────────────────────────────────────────────

@app.post("/api/code/run")
def code_run_endpoint(body: CodeRunRequest) -> dict[str, Any]:
    from local_mind.code_exec import run_code

    timeout = max(1, min(body.timeout, 120))
    result = run_code(body.code, body.language, timeout=timeout, stdin=body.stdin)
    return result.to_dict()


@app.post("/api/code/format")
def code_format_endpoint(body: CodeFormatRequest) -> dict[str, str]:
    from local_mind.code_exec import format_code

    return {"code": format_code(body.code, body.language)}


@app.get("/api/code/runtimes")
def code_runtimes() -> dict[str, str | None]:
    from local_mind.code_exec import available_runtimes

    return available_runtimes()


# ── Knowledge / Learning ─────────────────────────────────────────────────────

@app.post("/api/learn/url")
def learn_url(body: LearnUrlRequest) -> dict[str, Any]:
    try:
        return knowledge.learn_url(body.url)
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/learn/text")
def learn_text(body: LearnTextRequest) -> dict[str, Any]:
    try:
        return knowledge.learn_text(body.text, body.source)
    except Exception as e:
        raise HTTPException(500, str(e))


class KnowledgeQueryRequest(BaseModel):
    query: str
    top_k: int | None = None


@app.post("/api/knowledge/query")
def knowledge_query(body: KnowledgeQueryRequest) -> list[dict[str, Any]]:
    try:
        return knowledge.query(body.query, top_k=body.top_k)
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/knowledge/stats")
def knowledge_stats() -> dict[str, Any]:
    return knowledge.stats()


@app.post("/api/knowledge/clear")
def knowledge_clear() -> dict[str, str]:
    return knowledge.clear()


# ── Voice ─────────────────────────────────────────────────────────────────────

@app.post("/api/voice/transcribe")
async def voice_transcribe(
    audio: UploadFile = File(...),
    language: str | None = Query(None),
    model: str | None = Query(None),
) -> dict[str, str]:
    data = await audio.read()
    if len(data) < 100:
        raise HTTPException(400, "Audio too short")
    try:
        from local_mind.voice import transcribe_bytes

        text = transcribe_bytes(
            data,
            language=language,
            model_size=model,
            filename=audio.filename,
        )
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, str(e))
    return {"text": text}


@app.post("/api/voice/transcribe/stream")
async def voice_transcribe_stream(
    audio: UploadFile = File(...),
    language: str | None = Query(None),
    model: str | None = Query(None),
) -> StreamingResponse:
    data = await audio.read()
    if len(data) < 100:
        raise HTTPException(400, "Audio too short")
    filename = audio.filename

    def sse():
        try:
            from local_mind.voice import transcribe_stream

            for evt in transcribe_stream(
                data,
                language=language,
                model_size=model,
                filename=filename,
            ):
                yield f"data: {json.dumps(evt)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        sse(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Assistant (JARVIS-style voice loop) ──────────────────────────────────────

class AssistantCommand(BaseModel):
    text: str
    speak: bool = True


@app.get("/api/assistant/status")
def assistant_status() -> dict[str, Any]:
    from local_mind.assistant import get_engine

    return get_engine().status()


@app.post("/api/assistant/start")
def assistant_start() -> dict[str, Any]:
    from local_mind.assistant import get_engine

    engine = get_engine()
    try:
        engine.start()
    except Exception as e:
        raise HTTPException(500, str(e))
    return engine.status()


@app.post("/api/assistant/stop")
def assistant_stop() -> dict[str, Any]:
    from local_mind.assistant import get_engine

    engine = get_engine()
    engine.stop()
    return engine.status()


@app.post("/api/assistant/command")
def assistant_command(body: AssistantCommand) -> dict[str, Any]:
    from local_mind.assistant import get_engine

    return get_engine().run_command(body.text, speak=body.speak)


@app.post("/api/assistant/trigger")
def assistant_trigger() -> dict[str, Any]:
    from local_mind.assistant import get_engine

    engine = get_engine()
    if not engine.status().get("running"):
        raise HTTPException(400, "Assistant is not running.")
    engine.trigger()
    return {"status": "triggered"}


@app.get("/api/assistant/apps")
def assistant_apps(limit: int = Query(200, ge=1, le=1000)) -> list[dict[str, Any]]:
    from local_mind.assistant import get_engine

    return get_engine().list_apps(limit=limit)


@app.post("/api/assistant/apps/refresh")
def assistant_apps_refresh() -> dict[str, Any]:
    from local_mind.assistant import get_engine

    engine = get_engine()
    apps = engine._apps.force_refresh()  # type: ignore[attr-defined]
    return {"total": len(apps)}


@app.get("/api/assistant/events")
async def assistant_events() -> StreamingResponse:
    import queue

    from local_mind.assistant import get_engine

    engine = get_engine()
    sub = engine.events.subscribe()

    def _get() -> dict[str, Any] | None:
        try:
            return sub.get(timeout=15.0)
        except queue.Empty:
            return None

    async def sse():
        try:
            while True:
                evt = await asyncio.to_thread(_get)
                if evt is None:
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {json.dumps(evt)}\n\n"
        finally:
            engine.events.unsubscribe(sub)

    return StreamingResponse(
        sse(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "model_loaded": model_manager.loaded,
        "model_name": model_manager.model_name,
        "knowledge_docs": knowledge.stats().get("documents", 0),
    }


# ── Serve React UI ───────────────────────────────────────────────────────────

dist = _ui_dist()
_index = dist / "index.html"

if _index.is_file():
    _assets = dist / "assets"
    if _assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(_assets)), name="ui-assets")

    @app.get("/")
    async def spa_index() -> FileResponse:
        return FileResponse(_index)

    @app.get("/favicon.svg")
    async def favicon() -> FileResponse:
        icon = dist / "favicon.svg"
        if not icon.is_file():
            raise HTTPException(404)
        return FileResponse(icon)
else:

    @app.get("/")
    async def ui_missing() -> HTMLResponse:
        return HTMLResponse(
            "<html><body style='font-family:system-ui;padding:2rem;max-width:42rem'>"
            "<h1>LocalMind</h1>"
            "<p>Web UI is not built. Run: <code>cd web-ui && npm ci && npm run build</code></p>"
            "<p>API is at <code>/api/</code>.</p></body></html>"
        )


# ── Server runner ────────────────────────────────────────────────────────────

def run_server(host: str | None = None, port: int | None = None) -> None:
    import uvicorn

    uvicorn.run(
        app,
        host=host or settings.host,
        port=port or settings.port,
        log_level="info",
    )
