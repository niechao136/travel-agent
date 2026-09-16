"""A2A 手工测试客户端：模拟"部分信息 → input-required → 补充 → resume"完整往返。

用法：
  uv run uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000   # 终端 1
  uv run python scripts/a2a_client_demo.py --base http://127.0.0.1:8000 --token <TOKEN>  # 终端 2
  # 可选：--protocol v03 走 0.3 兼容路径（无 A2A-Version header + message/send 方法）
"""

import argparse
import uuid
from typing import Any

import httpx

TIMEOUT = 300.0


def send(base: str, token: str, text: str, task_id: str | None = None,
         context_id: str | None = None, protocol: str = "v1") -> dict[str, Any]:
    if protocol == "v1":
        msg = {"messageId": uuid.uuid4().hex, "role": "ROLE_USER", "parts": [{"text": text}]}
        method = "SendMessage"
        headers = {"Authorization": f"Bearer {token}", "A2A-Version": "1.0"}
    else:
        msg = {
            "messageId": uuid.uuid4().hex,
            "role": "user",
            "kind": "message",
            "parts": [{"kind": "text", "text": text}],
        }
        method = "message/send"
        headers = {"Authorization": f"Bearer {token}"}
    if task_id:
        msg["taskId"] = task_id
    if context_id:
        msg["contextId"] = context_id
    payload = {"jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": method,
               "params": {"message": msg}}
    resp = httpx.post(f"{base}/a2a", json=payload, headers=headers, timeout=TIMEOUT)
    resp.raise_for_status()
    result = resp.json()["result"]
    return result.get("task", result)  # 1.0 嵌在 result.task 下；0.3 平铺


def main() -> None:
    parser = argparse.ArgumentParser(description="a2a demo client")
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--token", required=True)
    parser.add_argument("--protocol", choices=["v1", "v03"], default="v1")
    args = parser.parse_args()

    input_required = "TASK_STATE_INPUT_REQUIRED" if args.protocol == "v1" else "input-required"
    task = send(args.base, args.token, "我想出去玩，帮规划一下", protocol=args.protocol)
    while task["status"]["state"] == input_required:
        question = task["status"]["message"]["parts"][0]["text"]
        print(f"\nAGENT: {question}")
        reply = input("YOU> ").strip()
        task = send(args.base, args.token, reply, task_id=task["id"],
                    context_id=task["contextId"], protocol=args.protocol)

    print(f"\n最终状态: {task['status']['state']}")
    for artifact in task.get("artifacts", []):
        for part in artifact["parts"]:
            print(part["text"])


if __name__ == "__main__":
    main()
