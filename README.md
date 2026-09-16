# 旅游规划 Agent

对话式旅游规划 Agent：信息不全时中断追问，调高德地图 MCP 取真实数据，产出逐日行程。
完整规格见 `PLAN.md`，执行计划见 `docs/superpowers/plans/2026-09-16-travel-agent-implementation.md`。

## 快速开始

```powershell
uv sync --all-groups
Copy-Item .env.example .env   # 填入 OPENAI_API_KEY / AMAP_MCP_URL
uv run pytest -q              # 跑全部测试

uv run python scripts/issue_token.py --caller local-test   # 发放 token（记下输出）
uv run uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000

# 另开终端：
uv run python scripts/a2a_client_demo.py --base http://127.0.0.1:8000 --token <TOKEN>
```

## 架构

- `app/graph/`：LangGraph 图（extract_and_merge → check_required → ask_missing/build_itinerary → present_draft），`interrupt()` 承载"追问"与"预算超支"两类中断；状态经 SQLite checkpointer 持久化到 `data/checkpoints.db`（`thread_id = A2A task_id`）。
- `app/a2a_adapter.py`：LangGraph `__interrupt__` → A2A `input-required`；`thread_id = A2A task_id`；恢复用 `Command(resume=...)`。
- `app/mcp_client.py`：高德 MCP（Streamable HTTP）：天气/地理编码/POI。
- `app/auth.py` + `scripts/issue_token.py`：SHA256 哈希存储的 Bearer Token 鉴权。
