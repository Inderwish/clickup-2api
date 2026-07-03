# clickup-2api

把 **ClickUp Brain** 的 Web 内部接口逆向包装成 **OpenAI 兼容** 的 `/v1/chat/completions` API，可直接被 OpenAI SDK 或其他客户端调用，并支持动态模型列表。

## 工作原理

ClickUp Brain 的对话流程（逆向所得）：

1. **换取 session token**：用 access JWT 调 `GET /v2/sd/team/{ws}/access-token`，获得短期 session token（约 465 秒有效）
2. **PreloadAiResult**（HTTP GraphQL mutation）— 预热请求
3. **AskAISubscription**（WebSocket GraphQL Subscription，`graphql-transport-ws` 协议）— 订阅 AI 回答，通过 `answerChunk` 流式接收
4. 用户问题通过 URL 参数 `q=/?keywords=<编码后的提问>` 传递
5. WS `connection_init` 的 payload 带 `{"Authorization": "Bearer <session_token>"}` 认证

**自动刷新**：2api 会在 session token 过期前自动用 access JWT 换取新 token。access JWT 自身过期后仍需重新从 ClickUp 获取。

## 首次使用：先配置三个值

首次启动前必须准备：

1. `CLICKUP_JWT`：ClickUp 登录凭据。
# jwt暂时每两天需要获取一次
2. `CLICKUP_WORKSPACE_ID`：ClickUp 工作区数字 ID。
3. `API_KEY`：你为本服务设置的访问密钥，不是 ClickUp JWT。所有 OpenAI 客户端都要使用它。

Windows 下双击 `setup.cmd`，然后按提示输入：

```text
setup.cmd
```

也可以使用 PowerShell 7：

```powershell
pwsh ./setup.ps1
```

JWT 输入时不会回显；API key 可以自行输入，留空则自动生成。配置会以 UTF-8 原子写入 `.env`。已有完整配置时脚本不会覆盖；需要重新配置单账号可运行 `pwsh ./setup.ps1 -Force`。

### 获取 JWT 和 Workspace ID

- JWT：登录 `https://app.clickup.com`，打开浏览器 DevTools → Application → Cookies → `https://app.clickup.com`，复制 `cu-jwt` 的值。
- Workspace ID：一般就是网页url里的数字。

> JWT 和 API key 都属于敏感信息。不要粘贴到聊天、Issue、日志或提交到仓库。Access JWT 通常约两天过期，过期后需要重新获取并更新 `.env`。

服务默认地址为 `http://127.0.0.1:8787`。Docker 和 Python 启动方式使用同一份 `.env` 和同一个 `API_KEY`。

## Docker 启动方式

先启动 Docker Desktop，然后双击：

```text
start-docker.cmd
```

或运行：

```powershell
pwsh ./start-docker.ps1
```

首次配置不完整时，启动脚本会自动进入上述配置流程。随后它会实时显示镜像构建进度、后台启动容器并检查健康状态。容器默认只映射到宿主机 `127.0.0.1:8787`。

常用维护命令：

```powershell
docker compose logs -f api
docker compose restart api
docker compose down
```

## Python 启动方式

需要 Python 3.10 或更高版本。推荐直接双击一键启动脚本：

```text
start-python.cmd
```

或运行：

```powershell
pwsh ./start-python.ps1
```

脚本会自动完成首次配置检查、创建 `.venv`、安装依赖并在后台启动服务。日志位于 `logs/`。停止服务：

```text
stop-python.cmd
```

手动启动方式：

```powershell
pwsh ./setup.ps1
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

Docker 与 Python 服务不能同时占用同一个端口；切换前先执行 `docker compose down` 或 `stop-python.cmd`。

## 多账号配置

### 单账号

`.env` 里填：
```
CLICKUP_JWT=eyJhbGci...
```

### 多账号轮询（推荐）

支持多个 ClickUp 账号轮流使用，分摊负载、避免单号被限频。`.env` 里二选一：

**简单格式（只需 JWT，逗号分隔）：**
```
CLICKUP_ACCOUNTS=eyJhbGci...jwt1,eyJhbGci...jwt2,eyJhbGci...jwt3
```

**JSON 格式（支持 label）：**
```
CLICKUP_ACCOUNTS_JSON=[{"jwt":"eyJ...","label":"acc1"},{"jwt":"eyJ...","label":"acc2"}]
```

> 多账号需要手动编辑 `.env`。每个账号独立管理 session token；某账号连续失败 3 次会自动禁用 5 分钟，请求自动切换到下一个可用账号。

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | Docker 健康检查 |
| GET | `/v1/models` | 动态列出当前账号可用的聊天模型 |
| POST | `/v1/chat/completions` | OpenAI 兼容对话，支持 `stream` |

默认模型为 `claude-opus-4-8`（Claude Opus 4.8 直通版）。服务会从 ClickUp 动态拉取 Brain、Agent 和 Passthrough 模型；图片模型不会出现在聊天模型列表中。

### 使用示例

**curl:**
```bash
curl http://localhost:8787/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <你的 API_KEY>" \
  -d '{"model":"claude-opus-4-8","messages":[{"role":"user","content":"你好"}]}'
```

**OpenAI Python SDK:**
```python
from openai import OpenAI
client = OpenAI(base_url="http://localhost:8787/v1", api_key="<你的 API_KEY>")
r = client.chat.completions.create(
    model="claude-opus-4-8",
    messages=[{"role": "user", "content": "你好"}],
)
print(r.choices[0].message.content)
```

**流式:**
```python
stream = client.chat.completions.create(
    model="claude-opus-4-8",
    messages=[{"role": "user", "content": "写一首诗"}],
    stream=True,
)
for chunk in stream:
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="")
```

## 配置项

| 环境变量 | 说明 | 默认 |
|---|---|---|
| `CLICKUP_ACCOUNTS_JSON` | 多账号 JSON 格式 | - |
| `CLICKUP_ACCOUNTS` | 多账号简单格式 `jwt\|ws,...` | - |
| `CLICKUP_JWT` | 单账号 JWT | - |
| `CLICKUP_WORKSPACE_ID` | 单账号 workspace ID | - |
| `CLICKUP_MOCK` | =1 返回假数据不调 ClickUp | 0 |
| `API_KEY` | 2api 访问密钥，首次使用必须配置 | 无 |
| `HOST` | 监听地址；非本机地址必须设置 `API_KEY` | 127.0.0.1 |
| `PORT` | 服务端口 | 8787 |

`temperature`、`max_tokens` 和 `top_p` 无法映射到 ClickUp Brain。为兼容常见 OpenAI 客户端，服务会接收并静默忽略这些参数。

## 目录结构

- `main.py` — FastAPI 入口，OpenAI 兼容路由 + 自动刷新
- `clickup_client.py` — ClickUp Brain 客户端（自动换 token + WS GraphQL）
- `config.py` — 环境变量配置
- `models.py` — OpenAI 兼容请求/响应模型
- `setup.cmd` / `setup.ps1` — 首次交互配置 JWT、Workspace ID 和 API key
- `Dockerfile` / `docker-compose.yml` — 容器构建和运行配置
- `start-docker.cmd` / `start-docker.ps1` — Windows 一键启动入口
- `start-python.cmd` / `start-python.ps1` — Python 一键后台启动
- `stop-python.cmd` / `stop-python.ps1` — 停止 Python 后台服务
- `test_e2e.py` — 端到端测试
- `capture-guide.md` — 抓包指引

## 逆向细节

- **GraphQL 网关**：`https://frontdoor-search.clickup-prod.com/graphql/gateway`
- **WebSocket**：`wss://frontdoor-search.clickup-prod.com/graphql/gateway?c=gws-web-1`（`graphql-transport-ws` 子协议）
- **Token 签发**：`https://frontdoor-prod-us-east-2-2.clickup.com/v2/sd/team/{ws}/access-token`
- **Token 生命周期**：session token 通常约 8–9 分钟，access JWT 通常约 2 天
- **默认模型**：`claude-opus-4-8`；也可使用 `/v1/models` 返回的其他模型 ID

## 免责声明

仅用于个人学习与合法授权范围内使用。请遵守 ClickUp 服务条款；账号凭据请自行保管，不要提交到仓库。
