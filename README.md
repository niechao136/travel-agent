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

### 演示客户端协议选择

`a2a_client_demo.py` 默认走 1.0 协议（`SendMessage` + `A2A-Version: 1.0` header）；加 `--protocol v03`
可走 0.3 兼容路径（无 `A2A-Version` header + `message/send` 方法）：

```powershell
uv run python scripts/a2a_client_demo.py --base http://127.0.0.1:8000 --token <TOKEN> --protocol v03
```

### data/ 目录

`data/` 目录**无需手动创建**：首次启动服务或发放 token 时会自动创建，并在其中生成
`checkpoints.db`（图状态）与 `tokens.db`（API token）。该目录已在 `.gitignore` 中。

### 关于重启

A2A 任务记录存在 SDK 的 `InMemoryTaskStore` 中（进程内）；**服务重启后 SDK 侧的 task 记录会丢失**。
但图的对话状态（`TravelRequest`/`Itinerary` 等）持久化在 SQLite checkpointer（`data/checkpoints.db`，
`thread_id = A2A task_id`）。因此重启后若继续用同一 `task_id` 发起 resume，图状态仍可恢复，
但依赖 SDK task_store 的历史查询会不可用。

## Docker Compose 部署

```powershell
Copy-Item .env.example .env    # 填入 OPENAI_API_KEY / AMAP_MCP_URL
docker compose up -d --build

docker compose exec travel-agent python scripts/issue_token.py --caller local-test
docker compose logs -f travel-agent
```

对外端口为 **10101**（映射容器内 8000）：

```powershell
uv run python scripts/a2a_client_demo.py --base http://127.0.0.1:10101 --token <TOKEN>
```

- `./data` 直接挂载到容器 `/app/data`（bind mount）：`checkpoints.db` / `tokens.db` 落在宿主机本目录，`docker compose down` 或重建容器都不丢状态，`down -v` 也不影响它；目录不存在时 Docker 会自动创建（`.gitignore` 已忽略，`data/` 也已从构建上下文排除）。
- `.env` 通过 `env_file` 注入容器，不打进镜像（`.dockerignore` 已排除）。
- 镜像按 `uv.lock` 执行 `uv sync --frozen --no-dev`，依赖与本地一致。
- 换域名/端口无需改配置：AgentCard 的 `supportedInterfaces[].url` 由请求的 Host/Scheme 推导。

## 架构

- `app/graph/`：LangGraph 图（extract_and_merge → check_required → ask_missing/build_itinerary → present_draft），`interrupt()` 承载"追问"与"预算超支"两类中断；状态经 SQLite checkpointer 持久化到 `data/checkpoints.db`（`thread_id = A2A task_id`）。
- `app/a2a_adapter.py`：LangGraph `__interrupt__` → A2A `input-required`；`thread_id = A2A task_id`；恢复用 `Command(resume=...)`。
- `app/mcp_client.py`：高德 MCP（Streamable HTTP）：天气/地理编码/POI（景点/餐厅/酒店）。
- `app/auth.py` + `scripts/issue_token.py`：SHA256 哈希存储的 Bearer Token 鉴权。
- `app/agent_card.py` + `app/main.py`：`/.well-known/agent-card.json` 按请求的 Host/Scheme 补全接口地址，无需配置对外地址。
