"""端到端测试：启动服务 + 调用 /v1/chat/completions（非流式 + 流式 + 多模型）。"""
import subprocess
import sys
import time
import httpx
import os

from models import DEFAULT_MODEL_ID

os.chdir(os.path.dirname(os.path.abspath(__file__)))

proc = subprocess.Popen(
    [sys.executable, "main.py"],
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    encoding="utf-8",
    env={
        **os.environ,
        "CLICKUP_MOCK": "1",
        "API_KEY": "e2e-test-key",
        "HOST": "127.0.0.1",
        "PORT": "18787",
        "PYTHONUTF8": "1",
    },
)

try:
    base = "http://127.0.0.1:18787"
    client = httpx.Client(
        trust_env=False,
        timeout=60,
        headers={"Authorization": "Bearer e2e-test-key"},
    )
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            if client.get(f"{base}/v1/models", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.3)
    else:
        proc.terminate()
        try:
            out, _ = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, _ = proc.communicate(timeout=5)
        raise RuntimeError(f"服务未启动:\n{out}")

    # 健康检查无需 API key，供 Docker 使用
    health = httpx.get(f"{base}/health", timeout=3, trust_env=False)
    assert health.status_code == 200 and health.json() == {"status": "ok"}

    # 测试 /v1/models
    print("=== /v1/models ===")
    r = client.get(f"{base}/v1/models", timeout=10)
    print(f"  status={r.status_code}")
    import json
    models = r.json().get("data", [])
    print(f"  models count: {len(models)}")
    for m in models[:10]:
        print(f"    {m['id']} ({m['owned_by']})")
    if len(models) > 10:
        print(f"    ... +{len(models)-10} more")

    # 测试默认模型非流式
    print(f"\n=== /v1/chat/completions ({DEFAULT_MODEL_ID}, non-stream) ===")
    r = client.post(
        f"{base}/v1/chat/completions",
        json={
            "messages": [{"role": "user", "content": "请回复数字666"}],
            "temperature": 0.7,
            "top_p": 0.9,
            "max_tokens": 128,
        },
        timeout=60,
    )
    print(f"  status={r.status_code}")
    print(f"  body={r.text[:300]}")

    # 测试默认模型流式
    print(f"\n=== /v1/chat/completions ({DEFAULT_MODEL_ID}, stream) ===")
    with client.stream(
        "POST", f"{base}/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "请回复数字888"}], "stream": True},
        timeout=60,
    ) as r:
        print(f"  status={r.status_code}")
        count = 0
        for line in r.iter_lines():
            if line:
                count += 1
                if count <= 6 or "[DONE]" in line:
                    print(f"  [{count}] {line[:120]}")
        print(f"  total lines: {count}")

    print("\nDONE")
finally:
    if "client" in locals():
        client.close()
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
