from __future__ import annotations

import hashlib
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


def sha256_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _is_expired(expires_at: str | None) -> bool:
    """按真实时刻判断过期：解析为 datetime 比较（字符串字典序在非 UTC 偏移下会误判）。

    缺失时区的时间按 UTC 解释；解析失败视为已过期（安全默认，拒绝放行）。
    """
    if not expires_at:
        return False
    try:
        expires = datetime.fromisoformat(expires_at)
    except ValueError:
        return True
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    return expires <= datetime.now(UTC)


@dataclass
class TokenInfo:
    caller_name: str
    scopes: list[str]


class TokenStore:
    """api_tokens 表（对齐 PLAN.md 8）：id, token_hash, caller_name, scopes, status, created_at, expires_at, last_used_at"""

    def __init__(self, db_path: str):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS api_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_hash TEXT UNIQUE NOT NULL,
                caller_name TEXT NOT NULL,
                scopes TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                expires_at TEXT,
                last_used_at TEXT
            )
            """
        )
        self.conn.commit()

    def issue(self, caller_name: str, scopes: list[str] | None = None,
              expires_at: str | None = None) -> str:
        token = secrets.token_urlsafe(32)
        self.conn.execute(
            "INSERT INTO api_tokens (token_hash, caller_name, scopes, status, created_at, expires_at)"
            " VALUES (?, ?, ?, 'active', ?, ?)",
            (sha256_hash(token), caller_name, ",".join(scopes or []), _now_iso(), expires_at),
        )
        self.conn.commit()
        return token  # 明文只在发放时返回一次

    def verify(self, token: str) -> TokenInfo | None:
        row = self.conn.execute(
            "SELECT caller_name, scopes, expires_at FROM api_tokens"
            " WHERE token_hash = ? AND status = 'active'",
            (sha256_hash(token),),
        ).fetchone()
        if row is None:
            return None
        caller, scopes, expires_at = row
        if _is_expired(expires_at):
            return None
        self.conn.execute(
            "UPDATE api_tokens SET last_used_at = ? WHERE token_hash = ?",
            (_now_iso(), sha256_hash(token)),
        )
        self.conn.commit()
        return TokenInfo(caller_name=caller, scopes=[s for s in scopes.split(",") if s])

    def revoke(self, caller_name: str) -> int:
        cur = self.conn.execute(
            "UPDATE api_tokens SET status = 'revoked' WHERE caller_name = ?", (caller_name,)
        )
        self.conn.commit()
        return cur.rowcount


async def _send_401(send):
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"www-authenticate", b"Bearer"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": b'{"detail":"invalid or missing token"}'})


class BearerAuthMiddleware:
    """纯 ASGI 中间件：经 `app.add_middleware()` 注册，保护全部 HTTP 路由；发现与探活路径豁免。

    1.x 中 A2A 路由直接挂在 FastAPI 上，scope["path"] 为完整路径，故豁免前缀直接用 "/.well-known"；
    "/healthz" 为存活探针（容器编排需要匿名可探，参见 app/main.py）。
    """

    def __init__(self, app, store: TokenStore, exempt_prefixes: tuple[str, ...] = ("/.well-known", "/healthz")):
        self.app = app
        self.store = store
        self.exempt_prefixes = exempt_prefixes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"].startswith(self.exempt_prefixes):
            return await self.app(scope, receive, send)
        headers = {k: v for k, v in scope.get("headers", [])}
        auth = headers.get(b"authorization", b"").decode("latin-1")
        if not auth.startswith("Bearer "):
            return await _send_401(send)
        info = self.store.verify(auth.removeprefix("Bearer "))
        if info is None:
            return await _send_401(send)
        await self.app(scope, receive, send)
