import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse

from config import load_settings
from models import (
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatCompletionResponse,
    Choice,
    DeltaChoice,
    Message,
    ModelInfo,
    ModelsList,
    Usage,
    DEFAULT_MODEL_ID,
)
from clickup_client import ClickUpBrainClient

settings = load_settings()
client = ClickUpBrainClient(settings)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not settings.api_key:
        raise RuntimeError("API_KEY 未配置，请先运行 setup.ps1")
    if not settings.mock:
        client.start_keepalive()
        await client.fetch_models()
    try:
        yield
    finally:
        await client.stop_keepalive()


app = FastAPI(title="clickup-2api", version="0.1.0", lifespan=lifespan)


def _check_auth(authorization: Optional[str]):
    if settings.api_key:
        token = (authorization or "").removeprefix("Bearer ").strip()
        if token != settings.api_key:
            raise HTTPException(status_code=401, detail="invalid api key")


@app.get("/health", include_in_schema=False)
async def health():
    return {"status": "ok"}


@app.get("/v1/models")
async def list_models(authorization: Optional[str] = Header(default=None)):
    _check_auth(authorization)
    models = await client.list_models()
    if models:
        data = [
            ModelInfo(
                id=m.get("id", ""),
                object="model",
                owned_by=m.get("provider", "clickup"),
            )
            for m in models
            if m.get("type") in ("brain", "agent", "passthrough")
        ]
    else:
        data = [ModelInfo(id=DEFAULT_MODEL_ID, object="model", owned_by="anthropic")]
    return ModelsList(data=data)


@app.post("/v1/chat/completions")
async def chat_completions(
    req: ChatCompletionRequest,
    authorization: Optional[str] = Header(default=None),
):
    _check_auth(authorization)
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())
    if req.stream:
        return StreamingResponse(
            _stream(req, completion_id, created),
            media_type="text/event-stream",
        )
    try:
        text = await client.chat(req.messages, req.model)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"ClickUp upstream error: {exc}") from exc
    return ChatCompletionResponse(
        id=completion_id,
        created=created,
        model=req.model,
        choices=[
            Choice(index=0, message=Message(role="assistant", content=text), finish_reason="stop")
        ],
        usage=Usage(),
    )


async def _stream(req, completion_id, created) -> AsyncIterator[bytes]:
    try:
        async for delta in client.chat_stream(req.messages, req.model):
            chunk = ChatCompletionChunk(
                id=completion_id,
                created=created,
                model=req.model,
                choices=[DeltaChoice(index=0, delta={"content": delta})],
            )
            yield f"data: {chunk.model_dump_json()}\n\n".encode()
    except Exception as exc:
        error = {
            "error": {
                "message": f"ClickUp upstream error: {exc}",
                "type": "upstream_error",
            }
        }
        yield f"data: {json.dumps(error, ensure_ascii=False)}\n\n".encode("utf-8")
        yield b"data: [DONE]\n\n"
        return
    done = ChatCompletionChunk(
        id=completion_id,
        created=created,
        model=req.model,
        choices=[DeltaChoice(index=0, delta={}, finish_reason="stop")],
    )
    yield f"data: {done.model_dump_json()}\n\n".encode()
    yield b"data: [DONE]\n\n"


if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8787"))
    local_hosts = {"127.0.0.1", "localhost", "::1"}
    if host not in local_hosts and not settings.api_key:
        raise RuntimeError("监听非本机地址时必须设置 API_KEY")
    uvicorn.run(app, host=host, port=port)
