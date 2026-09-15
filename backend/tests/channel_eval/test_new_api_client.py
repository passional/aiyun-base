"""NewApiClient 集成层测试（任务 3.1）

- mock HTTP 层（httpx.MockTransport）验证只读方法与唯一写方法 create_eval_token
- 白名单强约束：公共方法面 = 只读集合 + create_eval_token，代码评审锚点自动化
- 指定渠道（pin）能力探测（probe_pin_capability）响应分类
"""

import re
from pathlib import Path

import httpx
import pytest

from app.modules.channel_eval.run import new_api_client as _nac_module
from app.modules.channel_eval.run.new_api_client import NewApiClient, NewApiError
from app.modules.channel_eval.run.new_api_client import (
    probe_pin_capability as _do_probe_pin,
)
from app.modules.channel_eval.run.new_api_client import test_admin_connection as _do_test_admin_connection


def _install(monkeypatch: pytest.MonkeyPatch, handler) -> dict:
    """把 NewApiClient 的 HTTP 出口替换为 MockTransport，并统计请求。"""
    calls: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    def _client(self, timeout=None):
        return httpx.AsyncClient(timeout=timeout, transport=httpx.MockTransport(wrapped), trust_env=False)

    monkeypatch.setattr(NewApiClient, "_client", _client)
    return {"calls": calls}


def _json_response(payload, status=200):
    return httpx.Response(status, json=payload)


def _channel_page(items, total=None, p=1, size=100):
    return _json_response({"success": True, "data": {"items": items, "total": total if total is not None else len(items)}})


async def test_list_channels_pagination(monkeypatch: pytest.MonkeyPatch) -> None:
    """分页拉全：items/total 形态翻页取完。"""
    page1 = [{"id": i, "name": f"c{i}", "status": 1, "group": "default", "priority": 0, "weight": 0, "models": "m1"} for i in range(100)]
    page2 = [{"id": 100, "name": "c100", "status": 1, "group": "default", "priority": 0, "weight": 0, "models": "m1"}]

    state = {"page": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/api/user/self" in url:
            return _json_response({"success": True, "data": {"id": 1, "username": "root"}})
        state["page"] += 1
        if state["page"] == 1:
            assert "p=1" in url
            return _channel_page(page1, total=101)
        return _channel_page(page2)

    ctx = _install(monkeypatch, handler)
    client = NewApiClient("https://api.example.com", admin_key="adm")
    channels = await client.list_channels()
    assert len(channels) == 101
    # 翻页拉全（不含 /api/user/self 的 New-Api-User 探测请求）
    channel_calls = [r for r in ctx["calls"] if "/api/channel/" in str(r.url)]
    assert len(channel_calls) == 2


async def test_list_logs_params(monkeypatch: pytest.MonkeyPatch) -> None:
    """管理级日志：按 token_name + 时间窗过滤。"""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        assert "/api/log/" in url
        assert "token_name=eval-run1" in url
        assert "start_timestamp=100" in url
        assert "end_timestamp=200" in url
        assert "Authorization" in request.headers and request.headers["Authorization"] == "Bearer adm"
        return _channel_page([{"id": 1, "use_time": 2, "other": '{"frt": 100}'}])

    _install(monkeypatch, handler)
    client = NewApiClient("https://api.example.com", admin_key="adm")
    logs = await client.list_logs("eval-run1", 100, 200)
    assert len(logs) == 1


async def test_list_self_logs_user_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """用户级日志：走测试凭证。"""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        assert "/api/log/self" in url
        assert request.headers["Authorization"] == "Bearer sk-user"
        return _channel_page([{"id": 9}])

    _install(monkeypatch, handler)
    client = NewApiClient("https://api.example.com", test_key="sk-user")
    logs = await client.list_self_logs(0, 1)
    assert len(logs) == 1


async def test_create_eval_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """创建评估令牌：POST 后按名查询取回；列表 key 为脱敏值时须经密钥接口取明文。

    回归（apiworld 联调）：新版 new-api 列表/查询接口只回脱敏 key（``key[:4]+"**********"+key[-4:]``），
    把脱敏值当密钥发请求会得到 401 Invalid token。
    """
    import json

    posts: list[dict] = []
    real_key = "sk-evalrun1-0123456789abcdef0123456789abcdef"
    masked_key = real_key[:4] + "**********" + real_key[-4:]

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.method == "POST" and "/api/token/" in url and url.endswith("/key"):
            return _json_response({"success": True, "data": {"key": real_key}})
        if request.method == "POST" and "/api/token/" in url:
            posts.append(json.loads(request.content))
            return _json_response({"success": True, "data": True})
        if "/api/token/" in url:
            return _channel_page([{"id": 33, "name": "eval-run1", "key": masked_key, "group": "default"}])
        return _channel_page([])

    _install(monkeypatch, handler)
    client = NewApiClient("https://api.example.com", admin_key="adm")
    token = await client.create_eval_token("eval-run1", "default")
    assert token["key"] == real_key
    assert "*" not in token["key"]
    assert posts[0]["name"] == "eval-run1"
    assert posts[0]["group"] == "default"


async def test_business_failure_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """success=false → NewApiError 携带平台消息。"""
    _install(monkeypatch, lambda request: _json_response({"success": False, "message": "无权进行此操作，未登录且未提供 access token"}))
    client = NewApiClient("https://api.example.com", admin_key="bad")
    with pytest.raises(NewApiError, match="无权进行此操作"):
        await client.list_channels()


async def test_http_error_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """HTTP 非 200 → NewApiError 携带状态码。"""
    _install(monkeypatch, lambda request: httpx.Response(500, text="boom"))
    client = NewApiClient("https://api.example.com", admin_key="adm")
    with pytest.raises(NewApiError, match="HTTP 500"):
        await client.list_channels()


async def test_admin_connection_success_and_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """管理凭证连通性探测（只读 /api/user/self）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if "/api/user/self" in str(request.url):
            auth = request.headers.get("Authorization", "")
            if auth == "Bearer good":
                return _json_response({"success": True, "data": {"id": 1, "username": "root"}})
            return _json_response({"success": False, "message": "无效的令牌"})
        return _json_response({"success": True, "data": []})

    _install(monkeypatch, handler)
    ok, message = await _do_test_admin_connection("https://api.example.com", "good")
    assert ok is True and "root" in message
    ok, message = await _do_test_admin_connection("https://api.example.com", "bad")
    assert ok is False and "无效" in message


async def test_admin_user_id_header_sent_and_probe_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """显式提供 admin_user_id 时：直接携带 New-Api-User 头，跳过 /api/user/self 探测。

    强制校验的 new-api 站点上探测本身也会被拦截，必须依赖平台登记的
    管理用户ID（设计见 migration 34a9c2f8b110）。
    """
    user_self_calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if "/api/user/self" in str(request.url):
            user_self_calls.append(request)
            return _json_response({"success": True, "data": {"id": 1, "username": "root"}})
        assert request.headers.get("New-Api-User") == "42"
        return _channel_page([{"id": 70, "name": "ch70", "status": 1, "group": "default", "priority": 0, "weight": 0, "models": "m1"}])

    _install(monkeypatch, handler)
    client = NewApiClient("https://api.example.com", admin_key="adm", admin_user_id="42")
    channels = await client.list_channels()
    assert len(channels) == 1
    assert user_self_calls == [], "已提供 admin_user_id 时不应再发 /api/user/self 探测"

    # 测试连接同样直接携带 New-Api-User 头
    ok, message = await _do_test_admin_connection("https://api.example.com", "adm", admin_user_id="42")
    assert ok is True and "root" in message


async def test_new_api_user_required_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    """401 且响应体含 New-Api-User、又未配置管理用户ID时，错误信息追加引导。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401, json={"message": "Unauthorized, New-Api-User header not provided", "success": False}
        )

    _install(monkeypatch, handler)
    ok, message = await _do_test_admin_connection("https://api.example.com", "adm")
    assert ok is False
    assert "New-Api-User" in message and "管理用户ID" in message

    # 已配置管理用户ID（请求头带上了但仍被拒）时不再重复引导
    ok2, message2 = await _do_test_admin_connection("https://api.example.com", "adm", admin_user_id="42")
    assert ok2 is False and "管理用户ID" not in message2


def test_client_write_method_whitelist() -> None:
    """白名单强约束（ADR-0001）：公共方法面仅含只读方法 + 唯一写方法 create_eval_token。

    这是代码评审锚点的自动化：任何新增写方法都会使本断言失败。
    """
    public = {name for name in dir(NewApiClient) if not name.startswith("_")}
    expected = {
        # 只读
        "list_channels",
        "list_tokens",
        "list_logs",
        "list_self_logs",
        "list_models",
        "get_admin_user",
        "get_token_key",
        # 写（仅一项：每次运行的专用评估令牌）
        "create_eval_token",
    }
    assert public == expected, f"NewApiClient 接口面越界: {public ^ expected}"


def test_client_no_extra_write_verbs_in_source() -> None:
    """源码级白名单兜底（ADR-0001）：模块内不得出现 PUT/PATCH/DELETE，POST 仅限两处已知调用点。

    公共方法面断言扫不到私有写辅助方法（``_`` 前缀），故这里从源码层面兜底：
    任何新增的写动词调用都会使本断言失败。
    """
    source = Path(_nac_module.__file__).read_text(encoding="utf-8")

    for verb in ("put", "patch", "delete"):
        assert not re.search(rf"\.{verb}\(", source), f"模块出现未列入白名单的写动词 .{verb}("

    post_lines = [ln.strip() for ln in source.splitlines() if ".post(" in ln]
    assert len(post_lines) == 2, f"POST 调用点数量越界（预期 2 处）: {post_lines}"
    assert any("_admin_headers()" in ln for ln in post_lines), "管理端 POST 调用点缺失"
    assert any("/v1/chat/completions" in ln for ln in post_lines), "客户端实测 POST 调用点缺失"


async def test_probe_pin_capability_classified(monkeypatch: pytest.MonkeyPatch) -> None:
    """指定渠道（pin）探测分类：400 无效渠道=可用；403 普通用户=非管理员；401=令牌无效。"""
    import json

    from app.modules.channel_eval.run import new_api_client as nac

    responses: list[tuple[int, str]] = [
        (400, '{"error": {"message": "无效的渠道 Id"}}'),
        (403, '{"error": {"message": "普通用户不支持指定渠道"}}'),
        (401, '{"error": {"message": "Invalid token"}}'),
        (500, "boom"),
    ]
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        status, body = responses[len(seen) - 1]
        return httpx.Response(status, content=body.encode())

    # probe_pin_capability 独立构造 httpx.AsyncClient，需在模块命名空间替换
    _OriginalAsyncClient = httpx.AsyncClient

    def _async_client(timeout=None, trust_env=False, **kwargs):
        return _OriginalAsyncClient(timeout=timeout, transport=httpx.MockTransport(handler), trust_env=False)

    monkeypatch.setattr(nac.httpx, "AsyncClient", _async_client)

    # 400 无效的渠道 Id → pin 可用（渠道不存在即 400，无上游调用）
    ok, message = await _do_probe_pin("https://api.example.com", "sk-eval")
    assert ok is True and "可用" in message
    # 探测请求：pin 到固定不存在的渠道号，body 含 model/messages
    body = json.loads(seen[0].content)
    assert seen[0].headers["Authorization"] == "Bearer sk-eval-999999"
    assert body["model"] and body["messages"]

    # 403 普通用户不支持指定渠道 → 非管理员
    ok, message = await _do_probe_pin("https://api.example.com", "sk-eval")
    assert ok is False and "普通用户" in message and "管理员" in message

    # 401 → 令牌无效或版本不支持
    ok, message = await _do_probe_pin("https://api.example.com", "sk-eval")
    assert ok is False and "401" in message

    # 其它状态 → 返回摘要
    ok, message = await _do_probe_pin("https://api.example.com", "sk-eval")
    assert ok is False and "HTTP 500" in message
