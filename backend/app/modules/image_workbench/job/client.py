"""绘图工作台 - 上游调用层（设计 D1/D4）

本文件是绘图工作台对上游图片服务的**唯一** HTTP 出口（配置侧的连通性探测与
生成侧的出图请求都经此）。按 ``dialect`` 分支的**只有参考图的上行形态**：

- ``ark``（火山方舟）：文生图与图生图**同一个端点** ``POST {base_url}/images/generations``，
  参考图作为与提示词同一请求体内的普通字段（``image`` 数组，``data:<mime>;base64,...``）；
- ``openai``：文生图 ``POST {base_url}/images/generations``，
  有参考图时改走 ``POST {base_url}/images/edits`` 的 ``multipart/form-data``。

**请求体里有哪些参数，只由能力声明决定，不由接入方式决定**（D4）：请求体以
``model`` / ``prompt`` 为基座，其余字段一律按参数键的**点号路径**逐层建出来
（``optimize_prompt_options.mode`` → ``{"optimize_prompt_options": {"mode": ...}}``）。
一个 OpenAI 兼容的模型，其清单里不声明 ``output_format`` 与 ``watermark`` 两个条目
（那是火山方舟的厂商扩展字段），它们自然不会被发送——因此「参数快照 == 实际发送的
参数集」恒成立，客户端不需要按接入方式过滤参数。

``base_url`` 是**完整 API 前缀**（含 ``/v1`` 或 ``/api/plan/v3``，ADR-0005）：本文件
只在其后追加资源路径，不推断也不补全版本段（``{base_url}/images/generations``）。
"""

import base64
import json
import mimetypes
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx

# 探测类调用超时（秒）；出图请求的超时由调用方按任务给出
_DEFAULT_TIMEOUT = 30.0
# 出图请求默认超时（秒）：上游出图通常是数十秒量级
_GENERATE_TIMEOUT = 300.0
# 错误摘要长度：够定位原因又不至于把上游整页 HTML 灌进日志
_ERROR_SNIPPET_LIMIT = 300


def nest_params(params: Mapping[str, Any] | None) -> dict[str, Any]:
    """参数键的点号路径 → 逐层嵌套的请求体片段（D4）。

    ``{"size": "2K", "optimize_prompt_options.mode": "standard"}`` →
    ``{"size": "2K", "optimize_prompt_options": {"mode": "standard"}}``。

    参数与上游字段因此一一对应，不设「本地名 → 上游名」的映射表——映射表是漂移的温床。
    """
    body: dict[str, Any] = {}
    for key, value in (params or {}).items():
        *parents, leaf = str(key).split(".")
        cursor = body
        for parent in parents:
            nested = cursor.get(parent)
            if not isinstance(nested, dict):
                nested = {}
                cursor[parent] = nested
            cursor = nested
        cursor[leaf] = value
    return body


def form_value(value: Any) -> str:
    """多部分表单字段值 → 字符串（表单字段没有类型，布尔与复合值各有固定写法）。"""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


class ImageUpstreamError(Exception):
    """上游调用失败（网络 / HTTP 非 2xx / 业务错误）"""

    def __init__(self, message: str, *, upstream_status: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = upstream_status


class ImageArtifact:
    """上游返回的单张图片：二选一的取图线索（``b64_json`` 直接可用，``url`` 需下载）。

    上游地址**24 小时后失效**，因此这里只把线索原样带出，由调用方立即取字节并转存
    （设计 D5）；本系统绝不把 ``url`` 作为长期访问方式存库。

    图层拆分时同一次响应里会带回**底图与若干图层**：底图没有 ``z_index``（用「无层级」
    而非「层级 0」表达「底图不是图层」，见 ``CONTEXT.md``），图层带层级、名称、描述与
    位置框；位置框的绝对与归一化两种表示都留——交互编辑将直接取归一化那份作为坐标标记，
    两者是同一个 0–999 坐标系。
    """

    __slots__ = ("b64_json", "bounding_box", "description", "name", "size", "url", "z_index")

    def __init__(
        self,
        *,
        url: str | None = None,
        b64_json: str | None = None,
        size: str | None = None,
        z_index: int | None = None,
        name: str | None = None,
        description: str | None = None,
        bounding_box: dict[str, Any] | None = None,
    ) -> None:
        self.url = url
        self.b64_json = b64_json
        # 产物宽高（如 "2048x2048"）：火山方舟会回该字段，缺失时为 None
        self.size = size
        self.z_index = z_index
        self.name = name
        self.description = description
        self.bounding_box = bounding_box

    @property
    def is_base(self) -> bool:
        """是否为底图（无层级即底图）。"""
        return self.z_index is None

    def __repr__(self) -> str:  # 避免把整段 base64 打进日志
        has_b64 = "yes" if self.b64_json else "no"
        return f"ImageArtifact(url={self.url!r}, b64_json={has_b64}, size={self.size!r}, z_index={self.z_index!r})"


class ImageGenerationResult:
    """一次出图请求的结果：图片线索 + 上游 ``usage`` 原样 + 原始响应体。"""

    __slots__ = ("artifacts", "errors", "raw", "usage")

    def __init__(self, *, artifacts: list[ImageArtifact], usage: dict | None, raw: dict[str, Any], errors: list[str] | None = None) -> None:
        self.artifacts = artifacts
        self.errors = errors or []
        self.usage = usage
        self.raw = raw


def _snippet(body: bytes, limit: int = _ERROR_SNIPPET_LIMIT) -> str:
    try:
        text = body.decode("utf-8", errors="replace").strip()
    except Exception:  # noqa: BLE001
        text = ""
    return text[:limit] or "(空响应体)"


def describe_transport_error(method: str, url: str, error: httpx.HTTPError) -> str:
    """传输层异常 → 可读失败原因。

    ``httpx`` / ``httpcore`` 的传输异常多数**没有消息**（``str(e)`` 是空串，例如
    ``ReadError``）。只写 ``{e}`` 就得到一句「网络请求失败: 」，请求地址、失败环节、
    异常类型全部丢失——上游在请求体上传途中提前回错并断开连接时正是这种表现，
    现场只剩一句空话。因此这里补上「方法 + 路径」与异常链的类名。

    只取 URL 的 path：查询串可能含凭证，日志里不该出现。
    """
    target = httpx.URL(url).path
    names: list[str] = []
    node: BaseException | None = error
    while node is not None and len(names) < 3:
        names.append(type(node).__name__)
        node = node.__cause__ or node.__context__
    detail = str(error).strip()
    suffix = f"（{detail}）" if detail else ""
    return f"{method} {target} 传输层异常{suffix}：{' ← '.join(names)}"


def describe_http_error(resp: httpx.Response) -> str:
    """HTTP 非 2xx → 可读失败原因（优先取上游错误体里的业务说明）。"""
    detail = _snippet(resp.content)
    try:
        payload = json.loads(resp.content)
    except (ValueError, TypeError):
        return f"HTTP {resp.status_code}: {detail}"
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and error.get("message"):
            return f"HTTP {resp.status_code}: {error['message']}"
        if isinstance(error, str) and error:
            return f"HTTP {resp.status_code}: {error}"
        for key in ("message", "msg"):
            if payload.get(key):
                return f"HTTP {resp.status_code}: {payload[key]}"
    return f"HTTP {resp.status_code}: {detail}"


def encode_reference_image(path: str | Path) -> str:
    """本地参考图 → ``data:<mime>;base64,<...>`` 数据形式（ark 的同一请求体字段）。

    参考图**上传到本系统后落在本系统磁盘上**，这里提交的是图片数据本身，不是本系统
    地址——内网部署下本系统地址不可公网访问，提交地址必然失败（spec: generation /
    内网部署下的参考图上行）。
    """
    file_path = Path(path)
    raw = file_path.read_bytes()
    mime = mimetypes.guess_type(file_path.name)[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


class ImageUpstreamClient:
    """绘图上游客户端（httpx.AsyncClient 按请求创建，无连接泄漏）。

    ``_client()`` 是唯一的 HTTP 出口，测试经 monkeypatch 替换为 ``MockTransport``。
    """

    def __init__(
        self,
        base_url: str,
        *,
        api_key: str | None = None,
        dialect: str = "ark",
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.dialect = (dialect or "ark").strip() or "ark"
        self._api_key = (api_key or "").strip()
        self._timeout = timeout

    # ─────────────────────────── 基础请求 ───────────────────────────

    def _client(self, timeout: float | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout or self._timeout, trust_env=False)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    def _json_headers(self) -> dict[str, str]:
        headers = self._headers()
        headers["Content-Type"] = "application/json"
        return headers

    def _url(self, path: str) -> str:
        """完整 API 前缀 + 资源路径（前缀原样保留，不重复追加版本段）。"""
        return f"{self.base_url}/{path.lstrip('/')}"

    # ─────────────────────────── 连通性探测 ──────────────────────────

    async def list_models(self) -> list[str]:
        """只读模型列表探测（``GET {base_url}/models``）。

        连通性测试即用此调用：零成本、不产生任何生成任务，且返回上游的模型标识
        供管理员核对（spec: service-config / 连通性测试）。与渠道评估
        ``test_user_connection`` 用 ``/v1/models`` 探测凭证的做法一致。
        """
        url = self._url("/models")
        try:
            async with self._client() as client:
                resp = await client.get(url, headers=self._headers())
        except httpx.HTTPError as e:
            raise ImageUpstreamError(f"网络请求失败: {describe_transport_error('GET', url, e)}") from e
        if resp.status_code != 200:
            raise ImageUpstreamError(
                f"HTTP {resp.status_code}: {_snippet(resp.content)}",
                upstream_status=resp.status_code,
            )
        try:
            payload = resp.json()
        except ValueError as e:
            raise ImageUpstreamError(f"上游返回非 JSON 响应: {_snippet(resp.content)}") from e
        models: list[str] = []
        for item in payload.get("data") or []:
            if isinstance(item, dict) and item.get("id"):
                models.append(str(item["id"]))
        return sorted(set(models))

    # ─────────────────────────── 出图请求 ───────────────────────────

    @staticmethod
    def build_body(*, model: str, prompt: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """以 ``model`` / ``prompt`` 为基座、按参数键的点号路径逐层建出的请求体。

        ``model`` 与 ``prompt`` 不是参数（由任务的固定字段提供），参考图也不是参数
        （由参考图机制提供，见 ``build_ark_payload``）——三者都不走清单。
        """
        body: dict[str, Any] = {"model": model, "prompt": prompt}
        body.update(nest_params(params))
        return body

    def build_ark_payload(
        self,
        *,
        model: str,
        prompt: str,
        params: Mapping[str, Any] | None = None,
        reference_images: list[str] | None = None,
    ) -> dict[str, Any]:
        """火山方舟请求体（文生图与图生图同一个端点、同一份请求体结构）。

        参考图以 ``image`` 数组的 base64 数据形式置于**同一请求体**，与提示词平级。
        """
        body = self.build_body(model=model, prompt=prompt, params=params)
        if reference_images:
            body["image"] = [encode_reference_image(path) for path in reference_images]
        return body

    def build_openai_payload(self, *, model: str, prompt: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """OpenAI 兼容的文生图请求体。

        与 ark 的差别只有「参考图不内联」这一条（那条路径改走 ``/images/edits`` 多部分
        表单）。参数一律来自清单：清单没声明 ``output_format`` / ``watermark`` 的模型，
        即便走 ark 也不会发出这两个字段。
        """
        return self.build_body(model=model, prompt=prompt, params=params)

    async def request_generation(
        self,
        *,
        model: str,
        prompt: str,
        params: Mapping[str, Any] | None = None,
        reference_images: list[str] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """发起一次出图请求，返回上游原始响应体（解析见 ``generate``）。

        - ``ark``：``POST {base_url}/images/generations``，JSON 体（参考图内联在同一请求体）；
        - ``openai``：文生图 ``POST {base_url}/images/generations``（JSON）；
          有参考图时改走 ``POST {base_url}/images/edits`` 的 ``multipart/form-data``。
        """
        timeout = timeout or _GENERATE_TIMEOUT
        if self.dialect == "openai" and reference_images:
            return await self._request_openai_edit(
                model=model,
                prompt=prompt,
                params=params,
                reference_images=reference_images,
                timeout=timeout,
            )

        if self.dialect == "openai":
            payload = self.build_openai_payload(model=model, prompt=prompt, params=params)
        else:
            payload = self.build_ark_payload(model=model, prompt=prompt, params=params, reference_images=reference_images)
        return await self._post_json(self._url("/images/generations"), payload, timeout)

    async def _request_openai_edit(
        self,
        *,
        model: str,
        prompt: str,
        params: Mapping[str, Any] | None,
        reference_images: list[str],
        timeout: float,
    ) -> dict[str, Any]:
        """OpenAI 兼容的图生图：``POST {base_url}/images/edits`` 多部分表单。

        表单字段：参考图（``image``，多张时重复该字段）+ ``prompt`` + ``model`` +
        清单参数（点号键原样作字段名，复合值序列化为 JSON 字符串）。
        """
        url = self._url("/images/edits")
        form: dict[str, str] = {"model": model, "prompt": prompt}
        form.update({str(key): form_value(value) for key, value in (params or {}).items()})
        files: list[tuple[str, tuple[str, Any, str]]] = []
        opened: list[Any] = []
        try:
            for path in reference_images:
                file_path = Path(path)
                mime = mimetypes.guess_type(file_path.name)[0] or "image/png"
                handle = file_path.open("rb")
                opened.append(handle)
                files.append(("image", (file_path.name, handle, mime)))
            try:
                async with self._client(timeout) as client:
                    # 不手工设置 Content-Type：httpx 需自行生成 multipart 边界
                    resp = await client.post(url, headers=self._headers(), data=form, files=files)
            except httpx.HTTPError as e:
                raise ImageUpstreamError(f"网络请求失败: {describe_transport_error('POST', url, e)}") from e
        finally:
            for handle in opened:
                handle.close()
        return self._parse_json_response(resp)

    async def _post_json(self, url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        try:
            async with self._client(timeout) as client:
                resp = await client.post(url, headers=self._json_headers(), json=payload)
        except httpx.HTTPError as e:
            raise ImageUpstreamError(f"网络请求失败: {describe_transport_error('POST', url, e)}") from e
        return self._parse_json_response(resp)

    @staticmethod
    def _parse_json_response(resp: httpx.Response) -> dict[str, Any]:
        if resp.status_code != 200:
            raise ImageUpstreamError(describe_http_error(resp), upstream_status=resp.status_code)
        try:
            body = resp.json()
        except ValueError as e:
            raise ImageUpstreamError(f"上游返回非 JSON 响应: {_snippet(resp.content)}") from e
        if not isinstance(body, dict):
            raise ImageUpstreamError(f"上游响应结构异常: {_snippet(resp.content)}")
        return body

    # ─────────────────────────── 响应解析 ───────────────────────────

    @staticmethod
    def _item_error_message(item: dict[str, Any]) -> str | None:
        """``data[].error`` → 可读失败原因（兼容对象与字符串两种写法）。"""
        error = item.get("error")
        if isinstance(error, dict):
            for key in ("message", "msg", "code"):
                if error.get(key):
                    return str(error[key])
            return json.dumps(error, ensure_ascii=False)[:_ERROR_SNIPPET_LIMIT]
        if isinstance(error, str) and error:
            return error
        return None

    @staticmethod
    def _parse_bounding_box(item: dict[str, Any]) -> dict[str, Any] | None:
        """上游的位置框 → ``{"absolute": [...], "normalized": [...]}``（两种表示都留）。

        宽容三种写法：官方文档给出的带 ``absolute`` / ``normalized`` 的对象、只给出其中
        一种、以及直接给一个四元数组（那就是绝对表示）。都没有时返回 ``None``——底图
        本来就没有位置框。
        """
        raw = item.get("bounding_box")
        if isinstance(raw, dict):
            box = {key: list(raw[key]) for key in ("absolute", "normalized") if isinstance(raw.get(key), (list, tuple))}
            return box or None
        if isinstance(raw, (list, tuple)) and len(raw) == 4:
            return {"absolute": list(raw)}
        return None

    @classmethod
    def parse_generation_response(cls, payload: dict[str, Any]) -> ImageGenerationResult:
        """上游响应体 → 图片线索集合（同时兼容 ``data[].url`` 与 ``data[].b64_json``）。

        兼容面：清单里「返回格式」参数的默认值是网址形式，但上游仍可能回内联的
        ``b64_json``，两条路都要能吃下——两条路都在转存处被同一段逻辑收口。

        单图失败（``data[].error``）收集为可读原因；**一张都取不到**时抛
        ``ImageUpstreamError``，让任务落 ``failed`` 而不是留下一个空成功记录。
        """
        raw_items = payload.get("data")
        if not isinstance(raw_items, list) or not raw_items:
            # 上游有时把错误放在顶层（无 data 字段）
            top_error = cls._item_error_message(payload)
            if top_error:
                raise ImageUpstreamError(f"上游返回失败: {top_error}")
            raise ImageUpstreamError(f"上游响应不含图片数据: {json.dumps(payload, ensure_ascii=False)[:_ERROR_SNIPPET_LIMIT]}")

        artifacts: list[ImageArtifact] = []
        errors: list[str] = []
        for item in raw_items:
            if not isinstance(item, dict):
                errors.append(f"上游返回了非对象条目: {item!r}")
                continue
            message = cls._item_error_message(item)
            if message:
                errors.append(message)
                continue
            url = item.get("url")
            b64_json = item.get("b64_json")
            if not url and not b64_json:
                errors.append("上游返回的图片条目既无 url 也无 b64_json")
                continue
            # 图层字段只在图层拆分时出现；底图那条不带 z_index 也不带位置框，
            # 三个字段自然落 None，于是「底图」这件事不需要另立一个标志位
            z_index = item.get("z_index")
            artifacts.append(
                ImageArtifact(
                    url=str(url) if url else None,
                    b64_json=str(b64_json) if b64_json else None,
                    size=str(item["size"]) if item.get("size") else None,
                    z_index=int(z_index) if isinstance(z_index, (int, float, str)) and str(z_index).lstrip("-").isdigit() else None,
                    name=str(item["name"]) if item.get("name") else None,
                    description=str(item["description"]) if item.get("description") else None,
                    bounding_box=cls._parse_bounding_box(item),
                )
            )

        if not artifacts:
            raise ImageUpstreamError(f"上游未返回可用图片: {'；'.join(errors) or '原因未知'}")

        cls._mark_base(artifacts)
        usage = payload.get("usage")
        return ImageGenerationResult(
            artifacts=artifacts,
            usage=usage if isinstance(usage, dict) else None,
            raw=payload,
            errors=errors,
        )

    @staticmethod
    def _mark_base(artifacts: list[ImageArtifact]) -> None:
        """把「承载完整画面的那一张」认成底图：**它没有位置框，我们用 ``z_index`` 为空表示它**。

        这条转换是**实测逼出来的**（2026-09-17 在 ``/api/plan/v3`` 上核验）：真实上游在图层
        拆分时给**每一张**都编了 ``z_index``，底图是 ``0``，而不是像文档推断的那样省掉这个
        字段。原先「底图 = ``z_index`` 为空」的判据因此在真实响应上**永远不成立**——后果不止
        画布拿不到画面，连列表缩略图与详情里的「底图」标记都会一起失灵。

        判据取**「没有位置框」**而不是「``z_index`` 为 0」：证据上两者一致（实测中 z=0 那张
        正是没有框的那张），但前者与合成规则同源——图层都按各自位置框**紧裁**，底图没有框；
        上游哪天改成从 1 开始编号也仍然成立。上游的编号是上游细节，在入库边界翻译掉，
        下游（``CONTEXT.md`` 的「底图没有层级」、产物排序、缩略图、合成）就不必认识两套约定。

        没有位置框的条目不止一条时，取**层级最小**的那张当底图、其余原样保留：宁可按最保守的
        方式认，也不去猜「哪一张是完整画面」。一张都没有（上游给每张都配了框）就谁都不动。
        """
        candidates = [artifact for artifact in artifacts if artifact.bounding_box is None]
        if not candidates:
            return
        base = min(candidates, key=lambda artifact: (artifact.z_index is None, artifact.z_index or 0))
        base.z_index = None

    async def generate(
        self,
        *,
        model: str,
        prompt: str,
        params: Mapping[str, Any] | None = None,
        reference_images: list[str] | None = None,
        timeout: float | None = None,
    ) -> ImageGenerationResult:
        """发起出图请求并解析响应（调用方的入口方法）。"""
        payload = await self.request_generation(
            model=model,
            prompt=prompt,
            params=params,
            reference_images=reference_images,
            timeout=timeout,
        )
        return self.parse_generation_response(payload)

    async def fetch_image_bytes(self, artifact: ImageArtifact) -> bytes:
        """取图片字节：``b64_json`` 直接解码，否则立即下载 ``url``。

        ``url`` 由上游提供且**24 小时后失效**——调用方必须在拿到结果后立刻转存
        （设计 D5），因此这里不做任何缓存。
        """
        if artifact.b64_json:
            try:
                return base64.b64decode(artifact.b64_json)
            except Exception as e:  # noqa: BLE001
                raise ImageUpstreamError(f"上游返回的 b64_json 无法解码: {e}") from e
        if not artifact.url:
            raise ImageUpstreamError("上游未返回可取图片的 url 或 b64_json")
        try:
            async with self._client(_GENERATE_TIMEOUT) as client:
                resp = await client.get(artifact.url)
        except httpx.HTTPError as e:
            raise ImageUpstreamError(f"下载上游图片失败: {e}") from e
        if resp.status_code != 200:
            raise ImageUpstreamError(f"下载上游图片失败: HTTP {resp.status_code}: {_snippet(resp.content)}", upstream_status=resp.status_code)
        if not resp.content:
            raise ImageUpstreamError("下载上游图片失败: 响应体为空")
        return resp.content


__all__ = [
    "ImageArtifact",
    "ImageGenerationResult",
    "ImageUpstreamClient",
    "ImageUpstreamError",
    "describe_http_error",
    "encode_reference_image",
    "form_value",
    "nest_params",
]
