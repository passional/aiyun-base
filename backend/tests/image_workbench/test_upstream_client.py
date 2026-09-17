"""上游调用层测试（任务 3.1~3.3）

按 ``dialect`` 断言两条请求路径的 URL / 鉴权头 / Content-Type / 字段形态，
以及两种响应返回（``url`` 与 ``b64_json``）与错误映射。
"""

import base64
from pathlib import Path

import httpx
import pytest

from app.modules.image_workbench.job.client import (
    ImageUpstreamClient,
    ImageUpstreamError,
    encode_reference_image,
)

PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
)


def _install(monkeypatch: pytest.MonkeyPatch, handler) -> list[httpx.Request]:
    """把客户端的 HTTP 出口换成 MockTransport，并记录请求。"""
    calls: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    def _client(self, timeout=None):
        return httpx.AsyncClient(timeout=timeout, transport=httpx.MockTransport(wrapped), trust_env=False)

    monkeypatch.setattr(ImageUpstreamClient, "_client", _client)
    return calls


def _ok(payload: dict) -> httpx.Response:
    return httpx.Response(200, json=payload)


@pytest.fixture
def reference_image(tmp_path: Path) -> str:
    path = tmp_path / "ref.png"
    path.write_bytes(PNG_BYTES)
    return str(path)


# ─────────────────────────── 3.1 ark 分支 ───────────────────────────


async def test_ark_generation_uses_images_generations_with_multilevel_prefix(monkeypatch: pytest.MonkeyPatch, reference_image: str) -> None:
    """ark：出图请求发往 base_url + /images/generations，多级路径前缀原样保留。"""
    calls = _install(monkeypatch, lambda r: _ok({"data": [{"url": "https://up.example.com/a.png"}]}))
    client = ImageUpstreamClient("https://ark.cn-beijing.volces.com/api/plan/v3", api_key="sk-ark", dialect="ark")

    await client.request_generation(model="doubao-seedream-5-0-pro", prompt="一只猫")

    assert len(calls) == 1
    assert str(calls[0].url) == "https://ark.cn-beijing.volces.com/api/plan/v3/images/generations"
    assert calls[0].method == "POST"
    assert calls[0].headers["Authorization"] == "Bearer sk-ark"
    assert calls[0].headers["Content-Type"] == "application/json"


async def test_ark_generation_body_fields_and_inline_reference_image(monkeypatch: pytest.MonkeyPatch, reference_image: str) -> None:
    """ark：JSON 体含 model/prompt/size/output_format/watermark/response_format；
    参考图以 ``image`` 数组的 base64 数据形式置于**同一请求体**。"""
    calls = _install(monkeypatch, lambda r: _ok({"data": [{"url": "https://up.example.com/a.png"}]}))
    client = ImageUpstreamClient("https://ark.example.com/api/plan/v3", api_key="sk-ark", dialect="ark")

    await client.request_generation(
        model="doubao-seedream-5-0-pro",
        prompt="把背景换成雪地",
        size="2K",
        output_format="png",
        watermark=False,
        reference_images=[reference_image],
    )

    body = __import__("json").loads(calls[0].content)
    assert body["model"] == "doubao-seedream-5-0-pro"
    assert body["prompt"] == "把背景换成雪地"
    assert body["size"] == "2K"
    assert body["output_format"] == "png"
    assert body["watermark"] is False
    assert body["response_format"] == "url"
    # 参考图在同一请求体内、以数据形式给出（不是本系统地址）
    assert isinstance(body["image"], list) and len(body["image"]) == 1
    assert body["image"][0].startswith("data:image/png;base64,")
    assert base64.b64decode(body["image"][0].split(",", 1)[1]) == PNG_BYTES


async def test_ark_text_to_image_has_no_image_field_and_omits_absent_params(monkeypatch: pytest.MonkeyPatch) -> None:
    """ark：文生图不带 ``image`` 字段；未选择的参数不出现（不猜值）。"""
    calls = _install(monkeypatch, lambda r: _ok({"data": [{"url": "https://up.example.com/a.png"}]}))
    client = ImageUpstreamClient("https://ark.example.com/api/plan/v3", api_key="sk-ark", dialect="ark")

    await client.request_generation(model="m", prompt="p")

    body = __import__("json").loads(calls[0].content)
    assert "image" not in body
    assert "size" not in body and "output_format" not in body and "watermark" not in body
    assert body == {"model": "m", "prompt": "p", "response_format": "url"}


async def test_extra_params_do_not_override_explicit_params(monkeypatch: pytest.MonkeyPatch) -> None:
    """透传默认参数只填空缺，不覆盖表单/能力声明决定的显式参数。"""
    calls = _install(monkeypatch, lambda r: _ok({"data": [{"url": "https://up.example.com/a.png"}]}))
    client = ImageUpstreamClient("https://ark.example.com/api/plan/v3", api_key="sk-ark", dialect="ark")

    await client.request_generation(
        model="m",
        prompt="p",
        size="2K",
        extra_params={"size": "9K", "sequential_image_generation": "disabled", "response_format": "b64_json"},
    )

    body = __import__("json").loads(calls[0].content)
    assert body["size"] == "2K", "显式尺寸不得被透传项改写"
    assert body["response_format"] == "url", "响应形态由实现决定，不受透传项影响"
    assert body["sequential_image_generation"] == "disabled", "透传项应原样进入请求体"


async def test_encode_reference_image_uses_mime_and_base64(tmp_path: Path) -> None:
    """参考图编码为 ``data:<mime>;base64,`` 数据形式。"""
    path = tmp_path / "ref.jpg"
    path.write_bytes(b"\xff\xd8\xff\xe0jpeg-bytes")
    encoded = encode_reference_image(path)
    assert encoded.startswith("data:image/jpeg;base64,")
    assert base64.b64decode(encoded.split(",", 1)[1]) == b"\xff\xd8\xff\xe0jpeg-bytes"


async def test_network_error_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    """网络不可达 → 明确的上游错误（不是裸 httpx 异常）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    _install(monkeypatch, handler)
    client = ImageUpstreamClient("https://unreachable.example.com/v1", api_key="sk", dialect="ark")
    with pytest.raises(ImageUpstreamError) as exc:
        await client.request_generation(model="m", prompt="p")
    assert "网络请求失败" in str(exc.value)


# ─────────────────────────── 3.2 openai 分支 ───────────────────────────


async def test_openai_text_to_image_posts_generations_with_n_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """openai 文生图：``POST {base_url}/images/generations``，JSON 体含 ``n: 1``。"""
    calls = _install(monkeypatch, lambda r: _ok({"data": [{"url": "https://up.example.com/a.png"}]}))
    client = ImageUpstreamClient("https://apiworld.example.com/v1", api_key="sk-oa", dialect="openai")

    await client.request_generation(model="gpt-image-1", prompt="一只猫", size="1024x1024", output_format="png", watermark=True)

    assert len(calls) == 1
    assert str(calls[0].url) == "https://apiworld.example.com/v1/images/generations"
    assert calls[0].headers["Content-Type"] == "application/json"
    assert calls[0].headers["Authorization"] == "Bearer sk-oa"
    body = __import__("json").loads(calls[0].content)
    assert body["n"] == 1
    assert body["model"] == "gpt-image-1" and body["prompt"] == "一只猫" and body["size"] == "1024x1024"
    # 厂商扩展字段不下发给 OpenAI 兼容端点
    assert "output_format" not in body and "watermark" not in body


async def test_openai_reference_image_switches_to_edits_multipart(monkeypatch: pytest.MonkeyPatch, reference_image: str) -> None:
    """openai 参考图：改走 ``POST {base_url}/images/edits`` 的 multipart/form-data。"""
    calls = _install(monkeypatch, lambda r: _ok({"data": [{"b64_json": base64.b64encode(PNG_BYTES).decode()}]}))
    client = ImageUpstreamClient("https://apiworld.example.com/v1", api_key="sk-oa", dialect="openai")

    await client.request_generation(model="gpt-image-1", prompt="换成雪地", size="1024x1024", reference_images=[reference_image])

    assert len(calls) == 1
    assert str(calls[0].url) == "https://apiworld.example.com/v1/images/edits"
    assert calls[0].headers["Content-Type"].startswith("multipart/form-data; boundary=")
    assert calls[0].headers["Authorization"] == "Bearer sk-oa"
    raw = calls[0].content
    assert b'name="image"' in raw and b'name="prompt"' in raw and b'name="model"' in raw and b'name="size"' in raw
    assert "换成雪地".encode() in raw
    assert b"gpt-image-1" in raw
    assert PNG_BYTES[:8] in raw, "参考图应以文件字段原样上行"


async def test_openai_multiple_reference_images_repeat_image_field(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """openai 多张参考图：重复 ``image`` 字段逐个提交。"""
    paths = []
    for i in range(2):
        path = tmp_path / f"ref{i}.png"
        path.write_bytes(PNG_BYTES + bytes([i]))
        paths.append(str(path))

    calls = _install(monkeypatch, lambda r: _ok({"data": [{"b64_json": base64.b64encode(PNG_BYTES).decode()}]}))
    client = ImageUpstreamClient("https://apiworld.example.com/v1", api_key="sk-oa", dialect="openai")

    await client.request_generation(model="m", prompt="p", reference_images=paths)

    assert len(calls) == 1
    assert str(calls[0].url) == "https://apiworld.example.com/v1/images/edits"
    assert calls[0].content.count(b'name="image"') == 2, "两张参考图应产生两个 image 字段"


async def test_openai_extra_params_land_in_multipart_form(monkeypatch: pytest.MonkeyPatch, reference_image: str) -> None:
    """openai 图生图路径下，透传项以表单字段形式并入（显式字段仍优先）。"""
    calls = _install(monkeypatch, lambda r: _ok({"data": [{"b64_json": base64.b64encode(PNG_BYTES).decode()}]}))
    client = ImageUpstreamClient("https://apiworld.example.com/v1", api_key="sk-oa", dialect="openai")

    await client.request_generation(model="m", prompt="p", size="512x512", reference_images=[reference_image], extra_params={"quality": "high", "size": "9x9"})

    raw = calls[0].content
    assert b'name="quality"' in raw and b"high" in raw
    assert raw.count(b'name="size"') == 1 and b"512x512" in raw, "显式尺寸不得被透传项重复覆盖"


# ─────────────────────────── 3.3 响应解析 ───────────────────────────


async def test_url_response_is_downloaded_to_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    """``data[].url``：拿到线索后立即下载取字节；usage 原样保留。"""
    usage = {"generated_images": 1, "output_tokens": 4096}

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://up.example.com/a.png":
            return httpx.Response(200, content=PNG_BYTES)
        return _ok({"data": [{"url": "https://up.example.com/a.png"}], "usage": usage})

    calls = _install(monkeypatch, handler)
    client = ImageUpstreamClient("https://ark.example.com/api/plan/v3", api_key="sk", dialect="ark")

    result = await client.generate(model="m", prompt="p")

    assert len(result.artifacts) == 1
    assert result.artifacts[0].url == "https://up.example.com/a.png"
    assert result.artifacts[0].b64_json is None
    assert result.usage == usage, "上游 usage 必须原样保留"
    assert await client.fetch_image_bytes(result.artifacts[0]) == PNG_BYTES
    # 第二次请求就是产物下载
    assert str(calls[1].url) == "https://up.example.com/a.png"


async def test_b64_json_response_decodes_to_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    """``data[].b64_json``：直接解码取字节，无需再下载。"""
    encoded = base64.b64encode(PNG_BYTES).decode()
    calls = _install(monkeypatch, lambda r: _ok({"data": [{"b64_json": encoded}]}))
    client = ImageUpstreamClient("https://apiworld.example.com/v1", api_key="sk", dialect="openai")

    result = await client.generate(model="m", prompt="p")

    assert result.artifacts[0].b64_json == encoded and result.artifacts[0].url is None
    assert result.usage is None
    assert await client.fetch_image_bytes(result.artifacts[0]) == PNG_BYTES
    assert len(calls) == 1, "b64 路径不应再发下载请求"


async def test_single_image_error_in_data_becomes_readable_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """``data[].error``：单图失败转成可读失败原因（而不是空成功记录）。"""
    _install(monkeypatch, lambda r: _ok({"data": [{"error": {"code": "ContentPolicyViolation", "message": "请求被内容政策拦截"}}]}))
    client = ImageUpstreamClient("https://ark.example.com/api/plan/v3", api_key="sk", dialect="ark")

    with pytest.raises(ImageUpstreamError) as exc:
        await client.generate(model="m", prompt="p")
    assert "请求被内容政策拦截" in str(exc.value)


async def test_partial_failure_keeps_successful_image_and_records_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """上游回多条、其中一条失败：成功的那张照常可用，失败原因留痕。"""
    encoded = base64.b64encode(PNG_BYTES).decode()
    _install(monkeypatch, lambda r: _ok({"data": [{"b64_json": encoded}, {"error": "第二张超时"}]}))
    client = ImageUpstreamClient("https://ark.example.com/api/plan/v3", api_key="sk", dialect="ark")

    result = await client.generate(model="m", prompt="p")
    assert len(result.artifacts) == 1
    assert result.errors == ["第二张超时"]


@pytest.mark.parametrize(
    ("status", "payload", "expected"),
    [
        (401, {"error": {"message": "Invalid API key"}}, "HTTP 401: Invalid API key"),
        (429, {"error": {"message": "Rate limit exceeded"}}, "HTTP 429: Rate limit exceeded"),
        (400, {"code": "InvalidParameter", "message": "size 取值非法"}, "HTTP 400: size 取值非法"),
        (500, {"error": "internal error"}, "HTTP 500: internal error"),
    ],
)
async def test_upstream_http_errors_become_readable_reasons(monkeypatch: pytest.MonkeyPatch, status: int, payload: dict, expected: str) -> None:
    """上游 4xx/5xx：转为可读失败原因（带上游错误体里的业务说明与状态码）。"""
    _install(monkeypatch, lambda r: httpx.Response(status, json=payload))
    client = ImageUpstreamClient("https://ark.example.com/api/plan/v3", api_key="sk", dialect="ark")

    with pytest.raises(ImageUpstreamError) as exc:
        await client.generate(model="m", prompt="p")
    assert str(exc.value) == expected
    assert exc.value.status_code == status


async def test_response_without_data_is_a_readable_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """响应既无 data 也无错误信息 → 明确失败，不静默成功。"""
    _install(monkeypatch, lambda r: _ok({"detail": "unexpected"}))
    client = ImageUpstreamClient("https://ark.example.com/api/plan/v3", api_key="sk", dialect="ark")
    with pytest.raises(ImageUpstreamError) as exc:
        await client.generate(model="m", prompt="p")
    assert "不含图片数据" in str(exc.value)


async def test_non_json_response_is_a_readable_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """上游返回非 JSON（如网关 HTML 错误页）→ 明确失败。"""
    _install(monkeypatch, lambda r: httpx.Response(200, content=b"<html>502 Bad Gateway</html>"))
    client = ImageUpstreamClient("https://ark.example.com/api/plan/v3", api_key="sk", dialect="ark")
    with pytest.raises(ImageUpstreamError) as exc:
        await client.generate(model="m", prompt="p")
    assert "非 JSON 响应" in str(exc.value)
