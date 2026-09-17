from __future__ import annotations

from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.request_handlers.response_helpers import agent_card_to_dict
from a2a.server.routes.fastapi_routes import add_a2a_routes_to_fastapi
from a2a.server.routes.jsonrpc_routes import create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.a2a_adapter import TravelAgentExecutor
from app.agent_card import A2A_RPC_PATH, build_agent_card
from app.auth import BearerAuthMiddleware, TokenStore
from app.config import get_settings


def create_app(graph=None, auth_store: TokenStore | None = None) -> FastAPI:
    if graph is None:
        from app.graph.builder import default_graph

        graph = default_graph()

    if auth_store is None:
        auth_store = TokenStore(get_settings().auth_db_path)

    card = build_agent_card()
    handler = DefaultRequestHandler(
        agent_executor=TravelAgentExecutor(graph),
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )
    app = FastAPI(title="travel-planner-agent")

    def _card_response(request: Request) -> JSONResponse:
        """对外卡片按请求的 Host/Scheme 补全绝对地址，免配置且适配任意域名/端口。"""
        return JSONResponse(agent_card_to_dict(build_agent_card(str(request.base_url))))

    @app.get("/.well-known/agent-card.json")
    async def agent_card_well_known(request: Request) -> JSONResponse:
        return _card_response(request)

    @app.get("/.well-known/agent.json")
    async def agent_card_well_known_legacy(request: Request) -> JSONResponse:
        """A2A 0.3 的发现路径，兼容仍按旧路径取卡片的网关。"""
        return _card_response(request)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        """存活探针：不建图、不查库，仅确认进程可用（鉴权中间件已豁免）。"""
        return {"status": "ok"}

    add_a2a_routes_to_fastapi(
        app,
        jsonrpc_routes=create_jsonrpc_routes(
            handler, rpc_url=A2A_RPC_PATH, enable_v0_3_compat=True
        ),
    )
    app.add_middleware(BearerAuthMiddleware, store=auth_store)
    return app


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(create_app(), host="0.0.0.0", port=8000)
