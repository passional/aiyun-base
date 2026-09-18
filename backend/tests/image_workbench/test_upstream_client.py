"""上游调用层测试（任务 2.1、4.1）

按 ``dialect`` 断言两条请求路径的 URL / 鉴权头 / Content-Type / 字段形态，
以及两种响应返回（``url`` 与 ``b64_json``）与错误映射。

**请求体只由清单参数构成**（design D4）是这里最要紧的一条：接入方式只决定参考图的
上行形态，不再决定「哪些参数发得出去」。
"""

import base64
import json
from pathlib import Path

import httpx
import pytest

from app.modules.image_workbench.config.capability import ImageWorkbenchCapabilitySchema, resolve_params
from app.modules.image_workbench.job import client as client_module
from app.modules.image_workbench.job.client import (
    ImageUpstreamClient,
    ImageUpstreamError,
    encode_reference_image,
    nest_params,
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
    """ark：JSON 体的字段来自清单参数；参考图以 ``image`` 数组的 base64 数据形式
    置于**同一请求体**。"""
    calls = _install(monkeypatch, lambda r: _ok({"data": [{"url": "https://up.example.com/a.png"}]}))
    client = ImageUpstreamClient("https://ark.example.com/api/plan/v3", api_key="sk-ark", dialect="ark")

    await client.request_generation(
        model="doubao-seedream-5-0-pro",
        prompt="把背景换成雪地",
        params={"size": "2K", "output_format": "png", "watermark": False, "response_format": "url"},
        reference_images=[reference_image],
    )

    body = json.loads(calls[0].content)
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
    """ark：文生图不带 ``image`` 字段；没有的参数不出现（不猜值）。"""
    calls = _install(monkeypatch, lambda r: _ok({"data": [{"url": "https://up.example.com/a.png"}]}))
    client = ImageUpstreamClient("https://ark.example.com/api/plan/v3", api_key="sk-ark", dialect="ark")

    await client.request_generation(model="m", prompt="p", params={"size": "2K"})

    body = json.loads(calls[0].content)
    assert "image" not in body
    assert body == {"model": "m", "prompt": "p", "size": "2K"}


def test_dotted_param_keys_build_nested_body() -> None:
    """参数键就是上游字段路径，点号表示嵌套（design D4）。"""
    assert nest_params({"size": "2K", "optimize_prompt_options.mode": "standard"}) == {
        "size": "2K",
        "optimize_prompt_options": {"mode": "standard"},
    }
    # 多层路径逐层建出，同前缀的两条键合到同一个对象上
    assert nest_params({"a.b.c": 1, "a.b.d": 2, "a.e": 3}) == {"a": {"b": {"c": 1, "d": 2}, "e": 3}}
    assert nest_params({}) == {} and nest_params(None) == {}


async def test_ark_request_body_nests_dotted_param(monkeypatch: pytest.MonkeyPatch) -> None:
    """点号参数在真实请求体里呈现为嵌套对象。"""
    calls = _install(monkeypatch, lambda r: _ok({"data": [{"url": "https://up.example.com/a.png"}]}))
    client = ImageUpstreamClient("https://ark.example.com/api/plan/v3", api_key="sk-ark", dialect="ark")

    await client.request_generation(model="m", prompt="p", params={"optimize_prompt_options.mode": "fast"})

    body = json.loads(calls[0].content)
    assert body["optimize_prompt_options"] == {"mode": "fast"}
    assert "optimize_prompt_options.mode" not in body


def test_no_hardcoded_response_format_constant() -> None:
    """全仓不再存在硬编码的 ``response_format``：它已是一个可由管理员配置的参数条目。"""
    module_dir = Path(client_module.__file__).parent
    offenders = sorted(p.name for p in module_dir.rglob("*.py") if "response_format" in p.read_text(encoding="utf-8"))
    assert offenders == [], f"绘图工作台代码里不得再硬编码 response_format: {offenders}"
    assert not hasattr(client_module, "_RESPONSE_FORMAT")
    # config 侧同理：它只认「参数键」这个概念，不认识任何一个具体的参数名
    config_dir = module_dir.parent / "config"
    config_offenders = sorted(p.name for p in config_dir.rglob("*.py") if '"response_format"' in p.read_text(encoding="utf-8"))
    assert config_offenders == []


async def test_payload_is_built_only_from_manifest_params(monkeypatch: pytest.MonkeyPatch) -> None:
    """payload **只**由清单参数构成：清单没声明的字段不会出现在请求体里。

    这里用的是一个「OpenAI 兼容」的模型，其清单不声明 ``output_format`` 与 ``watermark``
    ——那两个是火山方舟的厂商扩展字段。参数的发与不发因此只由清单决定，客户端不需要
    按接入方式过滤参数。
    """
    capability = ImageWorkbenchCapabilitySchema.model_validate(
        {
            "reference": {"supported": True, "max_images": 4},
            "modes": [],
            "params": [
                {
                    "key": "size",
                    "label": {"zh": "尺寸", "en": "Size"},
                    "control": "select",
                    "allowed": ["1024x1024"],
                    "default": "1024x1024",
                    "source": "documented",
                },
                {
                    "key": "response_format",
                    "label": {"zh": "返回格式", "en": "Response format"},
                    "control": "select",
                    "allowed": ["url", "b64_json"],
                    "default": "url",
                    "source": "documented",
                },
            ],
        }
    )
    params, _ = resolve_params(capability, submitted={}, facts={})

    calls = _install(monkeypatch, lambda r: _ok({"data": [{"url": "https://up.example.com/a.png"}]}))
    client = ImageUpstreamClient("https://apiworld.example.com/v1", api_key="sk-oa", dialect="openai")

    await client.request_generation(model="gpt-image-1", prompt="一只猫", params=params)

    body = json.loads(calls[0].content)
    assert body == {"model": "gpt-image-1", "prompt": "一只猫", "size": "1024x1024", "response_format": "url"}
    assert "output_format" not in body and "watermark" not in body


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


async def test_openai_text_to_image_posts_generations_without_dialect_filtering(monkeypatch: pytest.MonkeyPatch) -> None:
    """openai 文生图：``POST {base_url}/images/generations``，JSON 体。

    请求体与 ark 的差别**只有参考图是否内联**这一条：参数一律来自清单，不再有
    「哪些字段该发」的接入方式分支。
    """
    calls = _install(monkeypatch, lambda r: _ok({"data": [{"url": "https://up.example.com/a.png"}]}))
    client = ImageUpstreamClient("https://apiworld.example.com/v1", api_key="sk-oa", dialect="openai")

    await client.request_generation(model="gpt-image-1", prompt="一只猫", params={"size": "1024x1024"})

    assert len(calls) == 1
    assert str(calls[0].url) == "https://apiworld.example.com/v1/images/generations"
    assert calls[0].headers["Content-Type"] == "application/json"
    assert calls[0].headers["Authorization"] == "Bearer sk-oa"
    body = json.loads(calls[0].content)
    assert body == {"model": "gpt-image-1", "prompt": "一只猫", "size": "1024x1024"}


async def test_openai_reference_image_switches_to_edits_multipart(monkeypatch: pytest.MonkeyPatch, reference_image: str) -> None:
    """openai 参考图：改走 ``POST {base_url}/images/edits`` 的 multipart/form-data。"""
    calls = _install(monkeypatch, lambda r: _ok({"data": [{"b64_json": base64.b64encode(PNG_BYTES).decode()}]}))
    client = ImageUpstreamClient("https://apiworld.example.com/v1", api_key="sk-oa", dialect="openai")

    await client.request_generation(model="gpt-image-1", prompt="换成雪地", params={"size": "1024x1024"}, reference_images=[reference_image])

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


async def test_openai_params_and_reference_images_all_land_in_multipart_form(monkeypatch: pytest.MonkeyPatch, reference_image: str) -> None:
    """openai 图生图路径下，清单参数以表单字段形式并入（点号键原样作字段名）。"""
    calls = _install(monkeypatch, lambda r: _ok({"data": [{"b64_json": base64.b64encode(PNG_BYTES).decode()}]}))
    client = ImageUpstreamClient("https://apiworld.example.com/v1", api_key="sk-oa", dialect="openai")

    await client.request_generation(
        model="m",
        prompt="p",
        params={"size": "512x512", "watermark": False, "optimize_prompt_options.mode": "fast"},
        reference_images=[reference_image],
    )

    raw = calls[0].content
    assert raw.count(b'name="size"') == 1 and b"512x512" in raw
    assert b'name="watermark"' in raw and b"false" in raw, "布尔参数按固定写法落到表单"
    assert b'name="optimize_prompt_options.mode"' in raw and b"fast" in raw


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


# ──────────────────── 4.1 图层拆分响应解析 ────────────────────

_LAYER_DECOMPOSITION_RESPONSE = {
    "model": "doubao-seedream-5-0-pro",
    "created": 1758000000,
    "data": [
        {"url": "https://up.example.com/base.png", "size": "2048x2048"},
        {
            "url": "https://up.example.com/layer_1.png",
            "size": "2048x2048",
            "z_index": 1,
            "name": "天空",
            "description": "整片天空与云",
            "bounding_box": {"absolute": [0, 0, 2048, 900], "normalized": [0, 0, 999, 439]},
        },
        {
            "url": "https://up.example.com/layer_2.png",
            "size": "2048x2048",
            "z_index": 2,
            "name": "人物",
            "description": "画面前景的人物",
            "bounding_box": {"absolute": [600, 900, 1400, 2000], "normalized": [292, 439, 683, 976]},
        },
    ],
    "usage": {"generated_images": 3, "output_tokens": 4096},
}


async def test_layer_decomposition_response_parses_every_layer_field(monkeypatch: pytest.MonkeyPatch) -> None:
    """图层拆分响应：底图 + 图层全部解析，图层带层级/名称/描述/位置框，底图不带位置框。"""
    _install(monkeypatch, lambda r: _ok(_LAYER_DECOMPOSITION_RESPONSE))
    client = ImageUpstreamClient("https://ark.example.com/api/plan/v3", api_key="sk", dialect="ark")

    result = await client.generate(model="m", prompt="p", params={"layer_decomposition": True})

    assert len(result.artifacts) == 3
    base, sky, person = result.artifacts

    # 底图：没有层级，也没有位置框与名称——「底图不是图层」不需要另立标志位
    assert base.is_base is True and base.z_index is None
    assert base.bounding_box is None and base.name is None
    assert base.url == "https://up.example.com/base.png" and base.size == "2048x2048"

    # 图层：层级、名称、描述、位置框的绝对与归一化两种表示都在
    assert sky.is_base is False and sky.z_index == 1
    assert sky.name == "天空" and sky.description == "整片天空与云"
    assert sky.bounding_box == {"absolute": [0, 0, 2048, 900], "normalized": [0, 0, 999, 439]}
    assert person.z_index == 2 and person.name == "人物"
    assert person.bounding_box["normalized"] == [292, 439, 683, 976]
    assert result.usage == {"generated_images": 3, "output_tokens": 4096}


@pytest.mark.parametrize(
    ("raw_box", "expected"),
    [
        ({"normalized": [1, 2, 3, 4]}, {"normalized": [1, 2, 3, 4]}),
        ([10, 20, 30, 40], {"absolute": [10, 20, 30, 40]}),
        ("1024,1024,2048,2048", None),
        (None, None),
    ],
)
def test_bounding_box_tolerates_documented_variants(raw_box: object, expected: dict | None) -> None:
    """位置框的宽容解析：带两种表示、只给一种、直接给四元数组；都给不出时为 None。"""
    item: dict = {"url": "https://up.example.com/a.png"}
    if raw_box is not None:
        item["bounding_box"] = raw_box
    assert ImageUpstreamClient._parse_bounding_box(item) == expected


def test_z_index_only_accepts_integer_like_values() -> None:
    """``z_index`` 不是整数时不猜：当作没有层级（宁可当底图，也不编一个层级出来）。"""
    parse = ImageUpstreamClient.parse_generation_response
    result = parse({"data": [{"url": "https://up.example.com/a.png", "z_index": "第一层"}]})
    assert result.artifacts[0].z_index is None


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


# ───────────────────── 传输层异常：报错不得是空的 ─────────────────────


def test_describe_transport_error_keeps_path_and_drops_query() -> None:
    """报错带「方法 + 路径 + 异常链」；查询串可能含凭证，不进日志。"""
    try:
        try:
            raise OSError("broken")
        except OSError as inner:
            raise httpx.ReadError("") from inner
    except httpx.ReadError as error:
        text = client_module.describe_transport_error(
            "POST", "https://ark.example.com/api/plan/v3/images/edits?token=SECRET", error
        )
    assert text == "POST /api/plan/v3/images/edits 传输层异常：ReadError ← OSError"
    assert "SECRET" not in text


def _broken_connection(_request: httpx.Request) -> httpx.Response:
    """模拟真实故障：连接在请求体上传途中被上游断开，异常消息为空。"""
    try:
        raise OSError("Connection reset by peer")
    except OSError as inner:
        raise httpx.ReadError("") from inner


async def test_transport_error_message_is_not_empty(monkeypatch: pytest.MonkeyPatch, reference_image: str) -> None:
    """参考图上传途中被断连 → 报错必须能看出「打去了哪里、断在哪一层」。

    回归现场：接入方式选了 OpenAI 兼容，参考图被发往这条入口上并不存在的
    ``/images/edits``；上游回 404 后立即断开，而客户端还在写请求体，于是抛出的
    ``httpx.ReadError`` 消息为空。旧文案只写 ``{e}``，日志里只剩一句
    「网络请求失败: 」——请求地址、失败环节、异常类型全部丢失。
    """
    _install(monkeypatch, _broken_connection)
    client = ImageUpstreamClient("https://ark.cn-beijing.volces.com/api/plan/v3", api_key="sk", dialect="openai")

    with pytest.raises(ImageUpstreamError) as exc:
        await client.request_generation(model="m", prompt="p", reference_images=[reference_image])

    message = exc.value.message
    assert message != "网络请求失败: "  # 旧文案（空消息）
    assert "POST /api/plan/v3/images/edits" in message  # 打去了哪个地址
    assert "ReadError" in message  # 断在哪一层
    assert " ← " in message  # 异常链


# ── 实测形状：上游给**每一张**都编 z_index，底图是 0（2026-09-17 在 /api/plan/v3 上核验） ──

# 取自真实响应的形状与量级：底图 z_index=0、无位置框、尺寸最大；图层 1..N 各带位置框
_MEASURED_LAYER_RESPONSE = {
    "model": "doubao-seedream-5-0-pro",
    "created": 1758000000,
    "data": [
        {"url": "https://up.example.com/a0.png", "size": "1086x1448", "z_index": 0},
        {
            "url": "https://up.example.com/a1.png",
            "size": "812x650",
            "z_index": 1,
            "name": "羽你更精彩装饰文字",
            "description": "提取左下角“羽你更精彩！”装饰文字图案",
            "bounding_box": {"absolute": [71, 1262, 284, 1432], "normalized": [65, 872, 261, 988]},
        },
        {
            "url": "https://up.example.com/a2.png",
            "size": "681x987",
            "z_index": 2,
            "name": "右下角卡通羽毛球人物",
            "bounding_box": {"absolute": [814, 1010, 1086, 1405], "normalized": [750, 698, 999, 970]},
        },
    ],
}


async def test_measured_shape_base_is_the_one_without_bounding_box(monkeypatch: pytest.MonkeyPatch) -> None:
    """实测形状：底图带 ``z_index = 0``，我们仍把它认成「没有层级」的那一张。

    上游给每张都编号（底图是 0），而我们的表示是「底图没有层级」（CONTEXT.md）。若不在这里
    翻译掉，底图会以「层级 0」入库，``is_base`` 永远为假——列表缩略图、详情里的「底图」标记与
    画布合成会一起失灵（这正是真实环境上发生的故障）。
    """
    _install(monkeypatch, lambda r: _ok(_MEASURED_LAYER_RESPONSE))
    client = ImageUpstreamClient("https://ark.example.com/api/plan/v3", api_key="sk", dialect="ark")

    result = await client.generate(model="m", prompt="p", params={"layer_decomposition": True})
    base, text, person = result.artifacts

    assert base.is_base is True and base.z_index is None, "没有位置框的那一张即底图"
    assert base.bounding_box is None and base.url == "https://up.example.com/a0.png"
    # 图层的层级原样保留（不去重编号，上游的层级就是叠放顺序）
    assert text.is_base is False and text.z_index == 1
    assert person.z_index == 2
    assert text.bounding_box["normalized"] == [65, 872, 261, 988]


async def test_base_is_not_invented_when_every_item_has_a_box(monkeypatch: pytest.MonkeyPatch) -> None:
    """每张都带位置框时谁都不动：宁可不认底图，也不凭空指定一张。"""
    payload = {"data": [
        {"url": "https://up.example.com/a.png", "z_index": 0, "bounding_box": [0, 0, 10, 10]},
        {"url": "https://up.example.com/b.png", "z_index": 1, "bounding_box": [5, 5, 20, 20]},
    ]}
    _install(monkeypatch, lambda r: _ok(payload))
    client = ImageUpstreamClient("https://ark.example.com/api/plan/v3", api_key="sk", dialect="ark")

    result = await client.generate(model="m", prompt="p")
    assert [artifact.z_index for artifact in result.artifacts] == [0, 1]


async def test_multiple_box_less_items_pick_the_lowest_level_as_base(monkeypatch: pytest.MonkeyPatch) -> None:
    """没有位置框的条目不止一条时取层级最小的当底图，其余原样保留（不猜哪一张是完整画面）。"""
    payload = {"data": [
        {"url": "https://up.example.com/a.png", "z_index": 2},
        {"url": "https://up.example.com/b.png", "z_index": 0},
        {"url": "https://up.example.com/c.png", "z_index": 1, "bounding_box": [0, 0, 10, 10]},
    ]}
    _install(monkeypatch, lambda r: _ok(payload))
    client = ImageUpstreamClient("https://ark.example.com/api/plan/v3", api_key="sk", dialect="ark")

    result = await client.generate(model="m", prompt="p")
    assert [artifact.z_index for artifact in result.artifacts] == [2, None, 1]
