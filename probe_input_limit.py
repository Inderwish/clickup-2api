"""probe_input_limit.py — 实测 ClickUp Brain 输入长度阈值。
用法: python probe_input_limit.py <N>
启动 main.py 子进程，发送 N 字符的 prompt，打印状态码/响应长度/错误，关闭服务。
"""
import os
import sys
import time
import subprocess
import signal

from models import DEFAULT_MODEL_ID

os.chdir(os.path.dirname(os.path.abspath(__file__)))
os.environ["PYTHONUTF8"] = "1"

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

N = int(sys.argv[1]) if len(sys.argv) > 1 else 20000
PORT = int(os.environ.get("PORT", "8787"))
API_KEY = os.getenv("API_KEY", "")
BASE = f"http://127.0.0.1:{PORT}"

prompt = "请只回复两个字符: OK\n" + "a" * N
print(f"[probe] N={N}, prompt len={len(prompt)} (chars), URL-encoded approx={len(prompt)*3} bytes")
sys.stdout.flush()

env = {**os.environ, "HOST": "127.0.0.1", "PORT": str(PORT)}
proc = subprocess.Popen(
    [sys.executable, "main.py"],
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    encoding="utf-8", env=env,
    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
)

import httpx

try:
    headers = {"Content-Type": "application/json"}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"
    client = httpx.Client(trust_env=False, headers=headers)
    deadline = time.time() + 20
    ok = False
    while time.time() < deadline:
        try:
            r = client.get(f"{BASE}/v1/models", timeout=2)
            if r.status_code == 200:
                ok = True
                break
        except Exception:
            pass
        time.sleep(0.5)
    if not ok:
        print("[probe] 服务未在 20s 内就绪")
    else:
        t0 = time.time()
        try:
            r = client.post(
                f"{BASE}/v1/chat/completions",
                json={"model": DEFAULT_MODEL_ID, "messages": [{"role": "user", "content": prompt}]},
                timeout=180,
            )
            dt = time.time() - t0
            body = r.text
            print(f"[probe] status={r.status_code} elapsed={dt:.1f}s body_len={len(body)}")
            print(f"[probe] body_head={body[:500]!r}")
            if r.status_code == 200:
                try:
                    j = r.json()
                    content = j.get("choices", [{}])[0].get("message", {}).get("content", "")
                    print(f"[probe] OK content_len={len(content)} content_head={content[:200]!r}")
                    print("[probe] RESULT=SUCCESS")
                except Exception as e:
                    print(f"[probe] 200 但解析失败: {e}")
                    print("[probe] RESULT=SUCCESS_BUT_BAD_JSON")
            else:
                print(f"[probe] RESULT=HTTP_FAIL_{r.status_code}")
        except httpx.ReadTimeout:
            print(f"[probe] RESULT=TIMEOUT after {time.time()-t0:.1f}s")
        except Exception as e:
            print(f"[probe] 请求异常: {type(e).__name__}: {e}")
            print(f"[probe] RESULT=EXC")
finally:
    if "client" in locals():
        client.close()
    try:
        if os.name == "nt":
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        proc.terminate()
    except Exception:
        pass
    try:
        proc.wait(timeout=8)
    except Exception:
        proc.kill()
    # 打印服务日志最后 30 行帮助诊断
    try:
        out, _ = proc.communicate(timeout=2)
    except Exception:
        out = ""
    if out:
        lines = out.splitlines()
        print("[probe] --- server log (tail 30) ---")
        for ln in lines[-30:]:
            print(f"  {ln}")
    print("DONE")
    sys.exit(0)
