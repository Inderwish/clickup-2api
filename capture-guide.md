# ClickUp Brain 抓包指引

目标：拿到 ClickUp Brain（AI 助手）在 Web 端发消息时调用的**真实内部 API 请求**，供后续逆向回填到 2api 服务。

不需要装任何抓包工具，用浏览器自带的 DevTools 就行。全程 5 分钟。

---

## 一、准备

1. 用 **Chrome 或 Edge** 浏览器（不要用无痕窗口，要保留登录态）。
2. 打开 https://app.clickup.com/ 并**登录**你的账号（需含 Brain 权限的付费版）。
3. 确认你能看到 / 打开 **Brain**（AI 助手）入口。一般在界面右下角的 Brain 图标，或顶部菜单里的 AI 按钮。

---

## 二、打开 DevTools

1. 在 ClickUp 页面按 **F12**（或右键 → 检查）打开开发者工具。
2. 切到 **Network（网络）** 标签。
3. 做三个设置（很重要）：
   - 勾选 **Preserve log（保留日志）** —— 切页面时不清空。
   - 勾选 **Disable cache（停用缓存）**。
   - 过滤器选 **Fetch/XHR**（只看接口请求，过滤掉图片/JS）。

---

## 三、抓一次 Brain 对话

1. 先点 Network 面板左上角的 **🚫 清除**按钮，把列表清空。
2. 在 ClickUp 里打开 **Brain**，输入一句简单的话，例如：
   ```
   你好，请用一句话介绍你自己
   ```
3. 按**回车 / 发送**，等 Brain 回答完。
4. 回到 Network 面板，看列表里新出现的请求。重点找：
   - **方法 = POST** 的请求
   - URL 里通常带 `brain`、`ai`、`chat`、`message`、`conversation`、`stream`、`v3` 等关键词
   - 时间点正好在你发送消息那一刻
   - **Type 列是 `fetch` 或 `xhr`**，如果响应是流式可能显示 `eventsource`

> 如果一次出现好几条 POST，把候选的都记下来；最关键的那条一般 **体积最大 / 耗时最长 / 响应里能看到 Brain 的回答文字**。

---

## 四、导出请求给我（两种方式任选）

### 方式 A：复制为 cURL（推荐，信息最全）

1. 在 Network 列表里**右键**那条 POST 请求 → **Copy（复制）** → **Copy as cURL (bash)**。
   - Edge 中文菜单：复制 → 以 cURL 格式复制 (bash)
   - Chrome 中文菜单：复制 → 以 cURL 格式复制 (bash)
2. 把整段 cURL 文本贴给我（直接粘贴到对话里）。

cURL 里会包含：URL、所有请求头（含 Cookie / Authorization）、请求体，可用于还原请求结构。

> ⚠️ cURL 里有登录凭据。分享前请把 Cookie、Authorization 和 JWT 的值替换成 `<REDACTED>`；真实值只保存在本机 `.env`，不要粘贴到聊天、Issue 或提交到仓库。

### 方式 B：手动抄 4 项信息

如果 cURL 复制不方便，请把以下 4 项贴给我：

1. **Request URL**（请求面板 Headers 标签最上面的 Request URL）
2. **Request Headers**（Headers 标签 → Request Headers 整段，尤其注意 `authorization`、`cookie`、任何 `x-` 开头的自定义头）
3. **Payload / Request Body**（Payload 标签 → view source 原始 JSON）
4. **Response 类型**：
   - 看 Response Headers 里有没有 `content-type: text/event-stream`（有 = SSE 流式）
   - 或者 Preview 标签里是不是一段一段 `data: {...}` 的内容
   - 把 Response 里**前几行**原样贴给我（答案文字可以打码，我要看格式）

---

## 五、还要再抓一个：新建会话 / 列表会话

Brain 一般还有这些接口，顺便抓一下能让 2api 更完整：

- **新建对话**：点 "New chat / 新对话" 时触发的请求（可能是 POST 创建 conversation，返回一个 conversation id）
- **历史会话列表**：打开 Brain 面板时拉取历史的请求（可能是 GET）

每个都按上面方式 A 复制 cURL 给我即可。

---

## 六、贴给我之后的流程

你把 cURL 贴回来后，我会：

1. 解析出真实端点、鉴权方式、请求体结构、响应格式（是否 SSE）。
2. 回填到 `clickup_client.py`，把占位的 `TODO` 换成真实调用。
3. 本地起服务跑通一次 `/v1/chat/completions`，验证流式 / 非流式都通。
4. 如有需要，再抓一次"多轮对话"的请求，确认 conversation id 怎么传递。

---

## 常见问题

**Q：Network 里找不到 POST 请求？**
- 确认过滤器是 Fetch/XHR 而不是 All；改成 All 再看一遍。
- 确认点了发送且 Brain 真的开始回答了。
- 某些版本 Brain 走 WebSocket（Network 过滤器选 WS）。如果是 WS，告诉我，我换抓包指引。

**Q：有好多条请求不知道哪条是？**
- 把发消息那一刻出现的所有 POST 的 URL 列表先发我，我帮你挑。

**Q：cURL 太长贴不下？**
- 存成文件把路径告诉我，或分两条消息贴。
