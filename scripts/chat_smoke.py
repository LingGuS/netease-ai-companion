"""对话冒烟测试脚本：向本地后端发消息，验证工具调用 / 流式输出 / 卡片协议。

用法:
    cd backend-agent  （或任意目录，只要 venv 正确）
    python scripts/chat_smoke.py <session_id> "<消息>" [--stream] [--base http://127.0.0.1:8000]

示例:
    python scripts/chat_smoke.py test-1 "我的账号信息是什么？" --stream
"""

import argparse
import json
import sys

import requests


def chat_blocking(base: str, session_id: str, message: str):
    r = requests.post(
        f"{base}/chat", json={"message": message, "session_id": session_id}, timeout=600
    )
    print(f"HTTP {r.status_code}")
    data = r.json()
    if r.status_code == 200:
        print(f"小乐: {data.get('reply')}")
        if data.get("cards"):
            print(f"cards: {len(data['cards'])} 张 -> {data['cards'][0].get('type')}")
    else:
        print(f"错误: {data}")


def chat_stream(base: str, session_id: str, message: str):
    tool_count = 0
    with requests.post(
        f"{base}/chat/stream",
        json={"message": message, "session_id": session_id},
        stream=True,
        timeout=(10, 300),
    ) as resp:
        print(f"HTTP {resp.status_code}")
        if resp.status_code != 200:
            print(f"错误: {resp.text[:200]}")
            return
        event = None
        for line in resp.iter_lines(decode_unicode=True):
            if not line:
                continue
            if line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data = json.loads(line.split(":", 1)[1].strip())
                if event == "token":
                    print(data.get("content", ""), end="", flush=True)
                elif event == "tool":
                    tool_count += 1
                    print(f"\n[工具] {data.get('message')}")
                elif event == "done":
                    cards = data.get("cards") or []
                    print(f"\n\n[完成] cards: {len(cards)} 张")
                    for c in cards:
                        print(f"  - {c.get('type')}: {c.get('payload', {}).get('name')}")
                elif event == "error":
                    print(f"\n[错误] {data.get('detail')}")
    print(f"\n（共 {tool_count} 次工具调用事件）")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="netease-ai-companion 对话冒烟测试")
    parser.add_argument("session_id")
    parser.add_argument("message")
    parser.add_argument("--stream", action="store_true", help="使用 SSE 流式端点")
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    args = parser.parse_args()

    if args.stream:
        chat_stream(args.base, args.session_id, args.message)
    else:
        chat_blocking(args.base, args.session_id, args.message)
