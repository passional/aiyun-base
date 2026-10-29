"""Host 头校验中间件回归测试。

重点固化两件事：
1. IPv6 字面量（``[::1]:8000`` / ``[::1]`` / ``::1``）不会被 Starlette 的 ``split(":")[0]`` 误判；
2. 其余语义与 Starlette 保持一致：端口忽略、``*.example.com`` 通配、``www.`` 301、非法主机 400。
"""

import httpx
import pytest
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.middlewares import CustomTrustedHostMiddleware, host_of


async def _ok_app(scope: Scope, receive: Receive, send: Send) -> None:
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
    await send({"type": "http.response.body", "body": b"ok"})


def _app(allowed: list[str], *, www_redirect: bool = True) -> ASGIApp:
    return CustomTrustedHostMiddleware(_ok_app, allowed_hosts=allowed, www_redirect=www_redirect)


def _client(allowed: list[str], *, www_redirect: bool = True) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=_app(allowed, www_redirect=www_redirect)),
                             base_url="http://testserver")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("127.0.0.1:8000", "127.0.0.1"),
        ("[::1]:8000", "::1"),
        ("[::1]", "::1"),
        ("::1", "::1"),
        ("Service.FastapiAdmin.com:443", "service.fastapiadmin.com"),
        ("", ""),
    ],
)
def test_host_of_normalizes_host(raw: str, expected: str) -> None:
    assert host_of(raw) == expected


@pytest.mark.parametrize("host", ["[::1]:8000", "[::1]", "::1"])
async def test_ipv6_hosts_are_allowed(host: str) -> None:
    """Starlette 原实现把 ``[::1]:8000`` 切成 ``"["``，这里必须放行。"""
    async with _client(["localhost", "127.0.0.1", "::1"]) as client:
        response = await client.get("/", headers={"host": host})
        assert response.status_code == 200


async def test_port_is_ignored() -> None:
    async with _client(["service.fastapiadmin.com"]) as client:
        response = await client.get("/", headers={"host": "service.fastapiadmin.com:8443"})
        assert response.status_code == 200


async def test_bracketed_pattern_in_allowlist_is_normalized() -> None:
    """允许清单里写成 ``[::1]``（settings.LOCALHOST_HOSTS 的形态）也应生效。"""
    async with _client(["[::1]"]) as client:
        response = await client.get("/", headers={"host": "[::1]:8000"})
        assert response.status_code == 200


async def test_wildcard_subdomain_allowed_but_bare_domain_rejected() -> None:
    async with _client(["*.fastapiadmin.com"]) as client:
        assert (await client.get("/", headers={"host": "a.fastapiadmin.com"})).status_code == 200
        assert (await client.get("/", headers={"host": "fastapiadmin.com"})).status_code == 400


async def test_host_comparison_is_case_insensitive() -> None:
    async with _client(["service.fastapiadmin.com"]) as client:
        response = await client.get("/", headers={"host": "SERVICE.FastapiAdmin.COM"})
        assert response.status_code == 200


async def test_invalid_host_returns_400() -> None:
    async with _client(["service.fastapiadmin.com"]) as client:
        response = await client.get("/", headers={"host": "evil.example.com"})
        assert response.status_code == 400
        assert "Invalid host header" in response.text


async def test_www_redirect() -> None:
    async with _client(["www.example.com"]) as client:
        response = await client.get("/path?x=1", headers={"host": "example.com"})
        assert response.status_code == 301
        assert response.headers["location"] == "http://www.example.com/path?x=1"


async def test_www_redirect_can_be_disabled() -> None:
    async with _client(["www.example.com"], www_redirect=False) as client:
        response = await client.get("/", headers={"host": "example.com"})
        assert response.status_code == 400


async def test_allow_any_wildcard() -> None:
    async with _client(["*"]) as client:
        response = await client.get("/", headers={"host": "anything.example"})
        assert response.status_code == 200


async def test_non_http_scope_passes_through() -> None:
    """lifespan 等非 http/ws scope 不应被拦截。"""
    called: list[str] = []

    async def _lifespan_app(scope: Scope, receive: Receive, send: Send) -> None:
        called.append(scope["type"])

    middleware = CustomTrustedHostMiddleware(_lifespan_app, allowed_hosts=["example.com"])
    await middleware({"type": "lifespan"}, _noop_receive, _noop_send)
    assert called == ["lifespan"]


async def test_websocket_with_invalid_host_is_closed() -> None:
    """WS scope 上不能发 HTTP 响应，应回 ``websocket.close``。"""
    sent: list[dict[str, object]] = []

    async def _ws_app(scope: Scope, receive: Receive, send: Send) -> None:
        sent.append({"type": "app_called"})

    async def _send(message: dict[str, object]) -> None:
        sent.append(message)

    middleware = CustomTrustedHostMiddleware(_ws_app, allowed_hosts=["example.com"])
    await middleware({"type": "websocket", "headers": [(b"host", b"evil.example")]}, _noop_receive, _send)
    assert sent == [{"type": "websocket.close", "code": 1008}]


async def _noop_receive() -> dict[str, object]:  # pragma: no cover - 供 ASGI 调用签名使用
    return {"type": "http.request"}


async def _noop_send(message: dict[str, object]) -> None:  # pragma: no cover - 供 ASGI 调用签名使用
    return None
