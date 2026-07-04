import asyncio
import base64
import itertools
import json
import os
import time
from typing import AsyncIterator, List, Optional, Tuple
from urllib.parse import quote

import httpx
import websockets

from config import Account, Settings, build_headers
from models import ChatMessage, DEFAULT_MODEL_ID


ASK_AI_SUBSCRIPTION = """
subscription AskAISubscription($q: String!, $conversationID: String, $jwt: String, $retried: Boolean, $selectedItems: String, $triggeredAtMs: String) {
  aiResult(
    q: $q
    conversationID: $conversationID
    jwt: $jwt
    retried: $retried
    selectedItems: $selectedItems
    triggeredAtMs: $triggeredAtMs
  ) {
    id
    answerChunk
    answerComplete
    reasoning
    title
    conversationID
    errorCode
    __typename
  }
}
"""

PRELOAD_MUTATION = """
mutation PreloadAiResult($q: String!, $conversationID: String, $retried: Boolean, $selectedItems: String) {
  preloadAiResult(
    q: $q
    conversationID: $conversationID
    retried: $retried
    selectedItems: $selectedItems
  ) {
    success
    preloadId
    reason
    __typename
  }
}
"""


class AccountSession:
    """单个 ClickUp 账号的 session 管理。"""

    SESSION_REFRESH_MARGIN = 60

    def __init__(self, account: Account, settings: Settings):
        self.account = account
        self.s = settings
        self.headers = build_headers(settings)
        # WS URL 从 base_url 推导：https:// → wss://
        _ws_base = settings.base_url.replace("https://", "wss://").replace("http://", "ws://")
        self.ws_url = f"{_ws_base}{settings.graphql_path}?c=gws-web-1"
        self.access_token_url = (
            f"{settings.access_token_base_url}"
            f"/v2/sd/team/{account.workspace_id}/access-token"
        )
        self._session_token: Optional[str] = None
        self._session_token_exp: float = 0.0
        self._token_lock = asyncio.Lock()
        self._refresh_task: Optional[asyncio.Task] = None
        self._fail_count: int = 0
        self._disabled: bool = False
        self._disabled_until: float = 0.0

    @property
    def label(self) -> str:
        return self.account.label

    def _check_jwt_expiry(self):
        jwt = self.account.jwt
        try:
            payload_b64 = jwt.split(".")[1]
            payload_b64 += "=" * (4 - len(payload_b64) % 4)
            payload = json.loads(base64.urlsafe_b64decode(payload_b64))
            exp = payload.get("exp")
            if not exp:
                return
            left = exp - time.time()
            if left < 0:
                print(f"[clickup-2api] {self.label}: Access JWT 已过期！")
            elif left < 3600:
                print(f"[clickup-2api] {self.label}: Access JWT 将在 {int(left/60)} 分钟后过期")
        except Exception:
            pass

    async def _fetch_session_token(self) -> Tuple[str, int]:
        jwt = self.account.jwt
        h = {
            "accept": "application/json, text/plain, */*",
            "authorization": f"Bearer {jwt}",
            "build-version": self.s.clickup_client_version,
            "origin": "https://app.clickup.com",
            "referer": "https://app.clickup.com/",
            "user-agent": self.headers.get("User-Agent", ""),
            "x-csrf": "1",
            "x-workspace-id": self.account.workspace_id,
        }
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as c:
            r = await c.get(self.access_token_url, headers=h)
            if r.status_code == 401:
                raise RuntimeError(f"{self.label}: Access JWT 无效或过期")
            r.raise_for_status()
            data = r.json()
        token = data["accessToken"]
        expires_in = int(data.get("expiresIn", 400))
        return token, expires_in

    async def get_session_token(self) -> str:
        if self._disabled and time.time() < self._disabled_until:
            raise RuntimeError(f"{self.label} 已被禁用")
        if self._disabled:
            self._disabled = False
            self._fail_count = 0
        async with self._token_lock:
            now = time.time()
            if (
                self._session_token
                and now < self._session_token_exp - self.SESSION_REFRESH_MARGIN
            ):
                return self._session_token
            token, expires_in = await self._fetch_session_token()
            self._session_token = token
            self._session_token_exp = now + expires_in
            return token

    async def _refresh_loop(self):
        while True:
            try:
                await self.get_session_token()
                delay = max(
                    5.0,
                    self._session_token_exp
                    - time.time()
                    - self.SESSION_REFRESH_MARGIN,
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f"[clickup-2api] {self.label}: session token 刷新失败: {e}")
                delay = 60.0
            await asyncio.sleep(delay)

    def start_keepalive(self):
        if self._refresh_task is None or self._refresh_task.done():
            self._refresh_task = asyncio.create_task(self._refresh_loop())

    async def stop_keepalive(self):
        if self._refresh_task is None:
            return
        self._refresh_task.cancel()
        try:
            await self._refresh_task
        except asyncio.CancelledError:
            pass
        self._refresh_task = None

    def mark_failure(self):
        self._fail_count += 1
        if self._fail_count >= 3:
            self._disabled = True
            self._disabled_until = time.time() + 300
            print(
                f"[clickup-2api] {self.label}: 连续失败 {self._fail_count} 次，禁用 5 分钟"
            )

    def mark_success(self):
        self._fail_count = 0

    def is_available(self) -> bool:
        if self._disabled and time.time() < self._disabled_until:
            return False
        return True

    def _build_q(self, user_content: str, selected_model: str = DEFAULT_MODEL_ID) -> str:
        keywords = quote(user_content, safe="")
        return (
            f"/?keywords={keywords}"
            f"&shouldTriage=false&createLink=false"
            f"&uiSurface=ai_full_page&AIToolId=ASSISTANT"
            f"&selectedModel={selected_model}"
        )

    async def _preload(
        self,
        session_token: str,
        user_content: str,
        model: str = DEFAULT_MODEL_ID,
    ) -> bool:
        url = f"{self.s.base_url}{self.s.graphql_path}?q=PreloadAiResult"
        h = dict(self.headers)
        h["Authorization"] = f"Bearer {session_token}"
        payload = {
            "operationName": "PreloadAiResult",
            "variables": {"q": self._build_q(user_content, model), "retried": False},
            "query": PRELOAD_MUTATION,
        }
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as c:
                r = await c.post(url, headers=h, json=payload)
                r.raise_for_status()
                data = r.json()
                if "errors" in data:
                    print(f"[clickup-2api] {self.label}: preload 返回 GraphQL 错误: {data['errors']}")
                    return False
                return True
        except Exception as e:
            print(f"[clickup-2api] {self.label}: preload 请求异常: {e}")
            return False

    async def _subscribe_ask(
        self,
        user_content: str,
        session_token: str,
        model: str = DEFAULT_MODEL_ID,
        conversation_id: Optional[str] = None,
    ) -> AsyncIterator[str]:
        variables = {
            "q": self._build_q(user_content, model),
            "jwt": self.account.jwt,
            "retried": False,
            "selectedItems": "[]",
            "triggeredAtMs": str(int(time.time() * 1000)),
        }
        if conversation_id:
            variables["conversationID"] = conversation_id
        sub_id = "1"
        subscribe_msg = json.dumps({
            "id": sub_id, "type": "subscribe",
            "payload": {"query": ASK_AI_SUBSCRIPTION, "variables": variables, "operationName": "AskAISubscription"},
        }, ensure_ascii=False)

        deadline = time.monotonic() + 300  # 整体 5 分钟超时

        _proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or True
        async with websockets.connect(
            self.ws_url,
            additional_headers={"Origin": "https://app.clickup.com", "User-Agent": self.headers.get("User-Agent", "")},
            subprotocols=["graphql-transport-ws"], max_size=2**20,
            open_timeout=30,
            proxy=_proxy,
        ) as ws:
            await ws.send(json.dumps({"type": "connection_init", "payload": {"Authorization": f"Bearer {session_token}"}}))
            ack_deadline = time.monotonic() + 15
            while True:
                remaining = ack_deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError("WebSocket 握手超时")
                raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                ack = json.loads(raw)
                ack_type = ack.get("type")
                if ack_type == "connection_ack":
                    break
                if ack_type == "ping":
                    await ws.send(json.dumps({"type": "pong"}))
                    continue
                if ack_type in {"error", "connection_error"}:
                    raise RuntimeError(f"WebSocket 鉴权失败: {ack.get('payload')}")
                raise RuntimeError(f"WebSocket 握手响应异常: {ack_type}")
            await ws.send(subscribe_msg)
            got_chunk = False
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError("WebSocket 订阅整体超时 (5 分钟)")
                recv_timeout = min(120, remaining)
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=recv_timeout)
                except (asyncio.TimeoutError, websockets.ConnectionClosed):
                    break
                msg = json.loads(raw)
                mtype = msg.get("type")
                if mtype == "ping":
                    await ws.send(json.dumps({"type": "pong"}))
                elif mtype == "next":
                    payload = msg.get("payload", {})
                    if errors := payload.get("errors"):
                        raise RuntimeError(f"GraphQL errors: {errors}")
                    ai = payload.get("data", {}).get("aiResult")
                    if not ai:
                        continue
                    if ai.get("errorCode"):
                        raise RuntimeError(f"Brain error: {ai['errorCode']}")
                    chunk = ai.get("answerChunk") or ""
                    complete = ai.get("answerComplete") or ""
                    if chunk:
                        got_chunk = True
                        yield chunk
                    elif complete and not got_chunk:
                        yield complete
                elif mtype == "error":
                    raise RuntimeError(f"WS error: {msg.get('payload')}")
                elif mtype == "complete":
                    break
            try:
                await ws.send(json.dumps({"id": sub_id, "type": "complete"}))
            except Exception:
                pass

    async def chat(self, prompt: str, model: str = DEFAULT_MODEL_ID) -> str:
        session_token = await self.get_session_token()
        if not await self._preload(session_token, prompt, model):
            raise RuntimeError("PreloadAiResult 失败")
        parts: list[str] = []
        async for chunk in self._subscribe_ask(prompt, session_token, model, None):
            parts.append(chunk)
        return "".join(parts)

    async def chat_stream(
        self,
        prompt: str,
        model: str = DEFAULT_MODEL_ID,
    ) -> AsyncIterator[str]:
        session_token = await self.get_session_token()
        if not await self._preload(session_token, prompt, model):
            raise RuntimeError("PreloadAiResult 失败")
        async for chunk in self._subscribe_ask(prompt, session_token, model, None):
            yield chunk


class ClickUpBrainClient:
    """多账号轮询客户端。"""

    def __init__(self, settings: Settings):
        self.s = settings
        if settings.accounts:
            self.sessions = [AccountSession(a, settings) for a in settings.accounts]
        else:
            self.sessions = []
        self._counter = itertools.count()
        self._lock = asyncio.Lock()
        self._models: list[dict] = []
        self._models_fetched: float = 0.0
        self._models_lock = asyncio.Lock()

    def start_keepalive(self):
        for s in self.sessions:
            s.start_keepalive()

    async def stop_keepalive(self):
        await asyncio.gather(
            *(s.stop_keepalive() for s in self.sessions),
            return_exceptions=True,
        )

    def _models_cache_valid(self) -> bool:
        if not self._models_fetched:
            return False
        ttl = 3600 if self._models else 60
        return time.time() - self._models_fetched < ttl

    async def _fetch_models_from_session(self, session: AccountSession) -> list[dict]:
        st = await session.get_session_token()
        h = dict(session.headers)
        h["Authorization"] = f"Bearer {st}"
        url = f"{self.s.base_url}{self.s.graphql_path}?q=getAIModels"
        payload = {"query": "query { getAIModels }"}
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as c:
            r = await c.post(url, headers=h, json=payload)
            r.raise_for_status()
            data = r.json()
        if errors := data.get("errors"):
            raise RuntimeError(f"GraphQL errors: {errors}")
        models = data.get("data", {}).get("getAIModels", [])
        if not isinstance(models, list):
            raise RuntimeError("getAIModels 返回格式异常")
        return [model for model in models if isinstance(model, dict) and model.get("id")]

    async def fetch_models(self) -> list[dict]:
        """从 ClickUp 拉取可用模型列表；成功缓存 1 小时，失败缓存 1 分钟。"""
        if self.s.mock:
            if not self._models:
                self._models = [
                    {
                        "id": DEFAULT_MODEL_ID,
                        "name": "Claude Opus 4.8 (mock)",
                        "provider": "anthropic",
                        "type": "passthrough",
                    }
                ]
                self._models_fetched = time.time()
            return self._models
        if self._models_cache_valid():
            return self._models
        async with self._models_lock:
            if self._models_cache_valid():
                return self._models
            attempted: set[int] = set()
            errors: list[str] = []
            while len(attempted) < len(self.sessions):
                session = await self._next_session()
                if not session or id(session) in attempted:
                    break
                attempted.add(id(session))
                try:
                    models = await self._fetch_models_from_session(session)
                    if models:
                        self._models = models
                        self._models_fetched = time.time()
                        return self._models
                    errors.append(f"{session.label}: 返回空列表")
                except Exception as exc:
                    errors.append(f"{session.label}: {exc}")
            self._models_fetched = time.time()
            if errors:
                print(f"[clickup-2api] 模型列表拉取失败: {'; '.join(errors)}")
            return self._models

    async def list_models(self) -> list[dict]:
        return await self.fetch_models()

    def _resolve_model(self, model_name: str) -> str:
        """将 OpenAI 请求的 model 名映射为 ClickUp selectedModel id。
        支持 id、name 模糊匹配，找不到时原样返回。"""
        aliases = {"clickup-brain": "brain"}
        model_name = aliases.get(model_name.lower(), model_name)
        if not self._models:
            return model_name
        for m in self._models:
            if m.get("id") == model_name:
                return m["id"]
        name_lower = model_name.lower()
        for m in self._models:
            if m.get("name", "").lower() == name_lower:
                return m["id"]
        for m in self._models:
            if name_lower in m.get("name", "").lower() or name_lower in m.get("id", "").lower():
                return m["id"]
        return model_name

    async def _next_session(self) -> Optional[AccountSession]:
        async with self._lock:
            available = [s for s in self.sessions if s.is_available()]
            if not available:
                return None
            idx = next(self._counter) % len(available)
            return available[idx]

    def _compose_prompt(self, messages: List[ChatMessage]) -> str:
        if not messages:
            raise ValueError("messages 不能为空")
        if len(messages) <= 1:
            return messages[-1].content
        history_parts = []
        for m in messages[:-1]:
            role_label = {"user": "User", "assistant": "Assistant", "system": "System"}.get(m.role, m.role)
            history_parts.append(f"{role_label}: {m.content}")
        history = "\n\n".join(history_parts)
        return f"Previous conversation:\n{history}\n\nCurrent question: {messages[-1].content}"

    async def chat(
        self,
        messages: List[ChatMessage],
        model: str = DEFAULT_MODEL_ID,
    ) -> str:
        if self.s.mock:
            return f"[clickup-2api mock] 你说的是：{messages[-1].content}"
        prompt = self._compose_prompt(messages)
        resolved = self._resolve_model(model)
        errors = []
        for _ in range(len(self.sessions)):
            session = await self._next_session()
            if not session:
                break
            try:
                result = await session.chat(prompt, resolved)
                session.mark_success()
                return result
            except Exception as e:
                session.mark_failure()
                print(f"[clickup-2api] {session.label} 失败: {type(e).__name__}: {e}，切换下一个账号")
                errors.append(f"{session.label}: {e}")
                continue
        raise RuntimeError(f"所有账号均失败: {'; '.join(errors)}")

    async def chat_stream(
        self,
        messages: List[ChatMessage],
        model: str = DEFAULT_MODEL_ID,
    ) -> AsyncIterator[str]:
        if self.s.mock:
            for ch in f"[clickup-2api mock] 流式：{messages[-1].content}":
                yield ch
            return
        prompt = self._compose_prompt(messages)
        resolved = self._resolve_model(model)
        errors = []
        for _ in range(len(self.sessions)):
            session = await self._next_session()
            if not session:
                break
            emitted = False
            try:
                async for chunk in session.chat_stream(prompt, resolved):
                    emitted = True
                    yield chunk
                session.mark_success()
                return  # 成功完成，直接返回
            except Exception as e:
                session.mark_failure()
                if emitted:
                    print(f"[clickup-2api] {session.label} 流式输出中断: {e}")
                    raise
                print(f"[clickup-2api] {session.label} 流式失败: {e}，切换下一个账号")
                errors.append(f"{session.label}: {e}")
                continue
        raise RuntimeError(f"所有账号流式均失败: {'; '.join(errors)}")
