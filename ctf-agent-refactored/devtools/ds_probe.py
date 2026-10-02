"""Probe DeepSeek (reasoning-style) tool-calling round trip using the .env key."""
import json
import os
import sys
from pathlib import Path

import httpx

BASE = "https://api.deepseek.com/v1"
MODELS = ["deepseek-flash", "deepseek-v4-pro"]


def read_key() -> str:
    for line in Path(".env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("DEEPSEEK_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("no key")


TOOLS = [{
    "type": "function",
    "function": {
        "name": "run_command",
        "description": "Run a shell command in the challenge workspace",
        "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]},
    },
}]

key = read_key()
headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def probe(client, model):
    print("=" * 60)
    print("MODEL", model)
    messages = [
        {"role": "system", "content": "You are a CTF solver. Use the tools. Be brief."},
        {"role": "user", "content": "List the files in the current directory using the run_command tool, then answer DONE."},
    ]
    r = client.post(f"{BASE}/chat/completions", headers=headers,
                    json={"model": model, "messages": messages, "tools": TOOLS, "tool_choice": "auto", "max_tokens": 2000})
    print("turn1 http:", r.status_code)
    if r.status_code != 200:
        print("turn1 body:", r.text[:300])
        return
    data = r.json()
    msg = data["choices"][0]["message"]
    print("turn1 keys:", sorted(msg.keys()))
    print("turn1 finish:", data["choices"][0].get("finish_reason"))
    print("turn1 content:", repr(msg.get("content"))[:200])
    print("turn1 reasoning:", repr(msg.get("reasoning_content"))[:150])
    calls = msg.get("tool_calls") or []
    print("turn1 tool_calls:", json.dumps(calls, ensure_ascii=False)[:300])
    if not calls:
        print("!! model did not call the tool")
        return

    # 按 coordinator 的方式回灌（content 可能是空串）
    messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
    messages.append({"role": "tool", "tool_call_id": calls[0].get("id", ""),
                     "content": json.dumps({"exit_code": 0, "stdout": "note.txt\nflag_hint.txt", "backend": "probe"})})
    r2 = client.post(f"{BASE}/chat/completions", headers=headers,
                     json={"model": model, "messages": messages, "tools": TOOLS, "tool_choice": "auto", "max_tokens": 2000})
    print("turn2 http:", r2.status_code)
    if r2.status_code != 200:
        print("turn2 body:", r2.text[:400])
        return
    d2 = r2.json()
    m2 = d2["choices"][0]["message"]
    print("turn2 finish:", d2["choices"][0].get("finish_reason"))
    print("turn2 content:", repr(m2.get("content"))[:300])
    print("usage:", d2.get("usage"))


models = sys.argv[1:] or MODELS
with httpx.Client(timeout=300) as client:
    for m in models:
        try:
            probe(client, m)
        except Exception as exc:  # noqa: BLE001
            print("ERROR", m, type(exc).__name__, str(exc)[:200])
