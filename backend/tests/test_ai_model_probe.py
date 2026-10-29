"""AI 接口探测（拉取模型列表 / 连通性测试）回归测试。

不访问真实网络：把 ``httpx.AsyncClient`` 替换为假客户端，验证 URL/请求头/请求体，
以及错误分支的异常文案（不得回显密钥）。
"""

from typing import Any

import httpx
import pytest

from app.core.exceptions import CustomException
from app.modules.ai.chat import service as ai_service


class _FakeResponse:
    def __init__(self, status_code: int = 200, payload: dict[str, Any] | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeClient:
    def __init__(self, response: _FakeResponse, error: Exception | None = None) -> None:
        self._response = response
        self._error = error
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False

    def _record(self, method: str, url: str, headers: dict[str, str] | None, body: Any = None) -> _FakeResponse:
        self.calls.append((method, url, {"headers": headers or {}, "json": body}))
        if self._error is not None:
            raise self._error
        return self._response

    async def get(self, url: str, headers: dict[str, str] | None = None) -> _FakeResponse:
        return self._record("GET", url, headers)

    async def post(self, url: str, headers: dict[str, str] | None = None, json: Any = None) -> _FakeResponse:
        return self._record("POST", url, headers, json)


def _patch(monkeypatch: pytest.MonkeyPatch, response: _FakeResponse, error: Exception | None = None) -> dict[str, _FakeClient]:
    holder: dict[str, _FakeClient] = {}

    def _factory(*args: Any, **kwargs: Any) -> _FakeClient:
        client = _FakeClient(response, error)
        holder["client"] = client
        return client

    monkeypatch.setattr(ai_service.httpx, "AsyncClient", _factory)
    return holder


async def test_probe_available_models_returns_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    holder = _patch(
        monkeypatch,
        _FakeResponse(200, {"data": [{"id": "gpt-4o", "owned_by": "openai"}, {"id": "deepseek-chat"}, {"no_id": 1}]}),
    )
    models = await ai_service.probe_available_models("https://api.example.com/v1/", "sk-secret")

    assert models == [{"id": "gpt-4o", "owned_by": "openai"}, {"id": "deepseek-chat", "owned_by": None}]
    method, url, payload = holder["client"].calls[0]
    assert (method, url) == ("GET", "https://api.example.com/v1/models")
    assert payload["headers"]["Authorization"] == "Bearer sk-secret"


async def test_probe_available_models_error_status_hides_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeResponse(401, {}))
    with pytest.raises(CustomException) as exc_info:
        await ai_service.probe_available_models("https://api.example.com/v1", "sk-secret")
    assert "HTTP 401" in str(exc_info.value)
    assert "sk-secret" not in str(exc_info.value)


async def test_probe_available_models_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeResponse(200, {}), error=httpx.ConnectError("boom"))
    with pytest.raises(CustomException) as exc_info:
        await ai_service.probe_available_models("https://api.example.com/v1", "sk-secret")
    assert "ConnectError" in str(exc_info.value)


async def test_probe_model_connection_sends_minimal_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    holder = _patch(monkeypatch, _FakeResponse(200, {"choices": [{"message": {"content": "p"}}]}))
    result = await ai_service.probe_model_connection("https://api.example.com/v1/", "sk-secret", "gpt-4o")

    assert result["ok"] is True
    assert result["model_id"] == "gpt-4o"
    assert isinstance(result["latency_ms"], int)
    method, url, payload = holder["client"].calls[0]
    assert (method, url) == ("POST", "https://api.example.com/v1/chat/completions")
    assert payload["json"]["model"] == "gpt-4o"
    assert payload["json"]["max_tokens"] == 1
    assert payload["headers"]["Authorization"] == "Bearer sk-secret"


async def test_probe_model_connection_error_status(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeResponse(404, {}))
    with pytest.raises(CustomException) as exc_info:
        await ai_service.probe_model_connection("https://api.example.com/v1", "sk-secret", "missing-model")
    assert "HTTP 404" in str(exc_info.value)
    assert "sk-secret" not in str(exc_info.value)
