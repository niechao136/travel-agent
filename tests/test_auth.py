import httpx

from app.auth import TokenStore
from app.main import create_app
from tests.fakes import FakeGraph, send_message


def make_store(tmp_path) -> TokenStore:
    return TokenStore(str(tmp_path / "tokens.db"))


def test_issue_and_verify(tmp_path):
    store = make_store(tmp_path)
    token = store.issue("test-caller")
    info = store.verify(token)
    assert info is not None and info.caller_name == "test-caller"
    assert store.verify("wrong-token") is None
    # 数据库中不存明文
    rows = store.conn.execute("SELECT token_hash FROM api_tokens").fetchall()
    assert token.encode() not in rows[0][0].encode()


def test_revoke_rejects_token(tmp_path):
    store = make_store(tmp_path)
    token = store.issue("test-caller")
    store.revoke("test-caller")
    assert store.verify(token) is None


def test_expired_token_rejected(tmp_path):
    store = make_store(tmp_path)
    token = store.issue("test-caller", expires_at="2000-01-01T00:00:00+00:00")
    assert store.verify(token) is None


async def test_api_401_without_token(tmp_path):
    app = create_app(graph=FakeGraph([]), auth_store=make_store(tmp_path))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post("/a2a", json=send_message("hi"), headers={"A2A-Version": "1.0"})
    assert resp.status_code == 401


async def test_api_200_with_valid_token(tmp_path):
    store = make_store(tmp_path)
    token = store.issue("tester")
    app = create_app(graph=FakeGraph([{"response_text": "# OK", "itinerary": {}}]), auth_store=store)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post(
            "/a2a",
            json=send_message("hi"),
            headers={"Authorization": f"Bearer {token}", "A2A-Version": "1.0"},
        )
    assert resp.status_code == 200


async def test_wellknown_exempt_from_auth(tmp_path):
    app = create_app(graph=FakeGraph([]), auth_store=make_store(tmp_path))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/.well-known/agent-card.json")
    assert resp.status_code == 200
