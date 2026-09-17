"""绘图工作台 - 上游调用层（设计 D1/D4）

本文件是绘图工作台对上游图片服务的**唯一** HTTP 出口（配置侧的连通性探测与
生成侧的出图请求都经此）。按 ``dialect`` 分支请求形态：

- ``ark``（火山方舟）：文生图与图生图**同一个端点** ``POST {base_url}/images/generations``，
  参考图作为与提示词同一请求体内的普通字段（``image`` 数组，``data:<mime>;base64,...``）；
- ``openai``：文生图 ``POST {base_url}/images/generations``（含 ``n: 1``），
  有参考图时改走 ``POST {base_url}/images/edits`` 的 ``multipart/form-data``。

``base_url`` 是**完整 API 前缀**（含 ``/v1`` 或 ``/api/plan/v3``，ADR-0005）：本文件
只在其后追加资源路径，不推断也不补全版本段（``{base_url}/images/generations``）。
"""

import base64
import json
import mimetypes
from pathlib import Path
from typing import Any

import httpx

# 探测类调用超时（秒）；出图请求的超时由调用方按任务给出
_DEFAULT_TIMEOUT = 30.0
# 出图请求默认超时（秒）：上游出图通常是数十秒量级
_GENERATE_TIMEOUT = 300.0
# 错误摘要长度：够定位原因又不至于把上游整页 HTML 灌进日志
_ERROR_SNIPPET_LIMIT = 300
# 出图请求的响应形态：url 体积小、兼容面最广（拿到后立即下载转存，见 D4/D5）
_RESPONSE_FORMAT = "url"


class ImageUpstreamError(Exception):
    """上游调用失败（网络 / HTTP 非 2xx / 业务错误）"""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class ImageArtifact:
    """上游返回的单张图片：二选一的取图线索（``b64_json`` 直接可用，``url`` 需下载）。

    上游地址**24 小时后失效**，因此这里只把线索原样带出，由调用方立即取字节并转存
    （设计 D5）；本系统绝不把 ``url`` 作为长期访问方式存库。
    """

    __slots__ = ("b64_json", "size", "url")

    def __init__(self, *, url: str | None = None, b64_json: str | None = None, size: str | None = None) -> None:
        self.url = url
        self.b64_json = b64_json
        # 产物宽高（如 "2048x2048"）：火山方舟会回该字段，缺失时为 None
        self.size = size

    def __repr__(self) -> str:  # 避免把整段 base64 打进日志
        has_b64 = "yes" if self.b64_json else "no"
        return f"ImageArtifact(url={self.url!r}, b64_json={has_b64}, size={self.size!r})"


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
        try:
            async with self._client() as client:
                resp = await client.get(self._url("/models"), headers=self._headers())
        except httpx.HTTPError as e:
            raise ImageUpstreamError(f"网络请求失败: {e}") from e
        if resp.status_code != 200:
            raise ImageUpstreamError(
                f"HTTP {resp.status_code}: {_snippet(resp.content)}",
                status_code=resp.status_code,
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
    def _merge_extra_params(extra_params: dict[str, Any] | None, explicit: dict[str, Any]) -> dict[str, Any]:
        """透传默认参数打底、显式参数覆盖。

        ``extra_params`` 只填空缺：``model``/``prompt``/``size`` 这类由工作台表单与
        能力声明共同决定的字段不得被配置里的透传项悄悄改写（否则用户所选参数与
        实际上行不一致，排查成本极高）。
        """
        body: dict[str, Any] = dict(extra_params or {})
        body.update({k: v for k, v in explicit.items() if v is not None})
        return body

    def build_ark_payload(
        self,
        *,
        model: str,
        prompt: str,
        size: str | None = None,
        output_format: str | None = None,
        watermark: bool | None = None,
        reference_images: list[str] | None = None,
        extra_params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """火山方舟请求体（文生图与图生图同一个端点、同一份请求体结构）。

        参考图以 ``image`` 数组的 base64 数据形式置于**同一请求体**，与提示词平级。
        """
        explicit: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "size": size,
            "output_format": output_format,
            "watermark": watermark,
            "response_format": _RESPONSE_FORMAT,
        }
        if reference_images:
            explicit["image"] = [encode_reference_image(p) for p in reference_images]
        return self._merge_extra_params(extra_params, explicit)

    def build_openai_payload(
        self,
        *,
        model: str,
        prompt: str,
        size: str | None = None,
        reference_images: list[str] | None = None,
        extra_params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """OpenAI 兼容的文生图请求体（设计 D4：``{model, prompt, size, n: 1, response_format}``）。

        不带 ``output_format``/``watermark``——那是火山方舟的厂商扩展字段（D4 的 ark 分支），
        发给 OpenAI 兼容端点只会被拒或不识别。参考图在这条路径上不内联，改走 ``/images/edits``。
        """
        explicit: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "size": size,
            "n": 1,
            "response_format": _RESPONSE_FORMAT,
        }
        return self._merge_extra_params(extra_params, explicit)

    async def request_generation(
        self,
        *,
        model: str,
        prompt: str,
        size: str | None = None,
        output_format: str | None = None,
        watermark: bool | None = None,
        reference_images: list[str] | None = None,
        extra_params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """发起一次出图请求，返回上游原始响应体（解析见 ``generate``）。

        - ``ark``：``POST {base_url}/images/generations``，JSON 体（参考图内联在同一请求体）；
        - ``openai``：文生图 ``POST {base_url}/images/generations``（JSON，含 ``n: 1``）；
          有参考图时改走 ``POST {base_url}/images/edits`` 的 ``multipart/form-data``。
        """
        timeout = timeout or _GENERATE_TIMEOUT
        if self.dialect == "openai" and reference_images:
            return await self._request_openai_edit(
                model=model,
                prompt=prompt,
                size=size,
                reference_images=reference_images,
                extra_params=extra_params,
                timeout=timeout,
            )

        url = self._url("/images/generations")
        if self.dialect == "openai":
            payload = self.build_openai_payload(model=model, prompt=prompt, size=size, extra_params=extra_params)
        else:
            payload = self.build_ark_payload(
                model=model,
                prompt=prompt,
                size=size,
                output_format=output_format,
                watermark=watermark,
                reference_images=reference_images,
                extra_params=extra_params,
            )
        return await self._post_json(url, payload, timeout)

    async def _request_openai_edit(
        self,
        *,
        model: str,
        prompt: str,
        size: str | None,
        reference_images: list[str],
        extra_params: dict[str, Any] | None,
        timeout: float,
    ) -> dict[str, Any]:
        """OpenAI 兼容的图生图：``POST {base_url}/images/edits`` 多部分表单。

        表单字段：参考图（``image``，多张时重复该字段）+ ``prompt`` + ``model`` + ``size``；
        ``extra_params`` 以字符串形式并入表单（管理员为逐模型配置的透传项，显式字段仍优先）。
        """
        url = self._url("/images/edits")
        form: dict[str, Any] = dict(extra_params or {})
        form.update({k: v for k, v in {"model": model, "prompt": prompt, "size": size, "n": 1, "response_format": _RESPONSE_FORMAT}.items() if v is not None})
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
                    resp = await client.post(url, headers=self._headers(), data={k: str(v) for k, v in form.items()}, files=files)
            except httpx.HTTPError as e:
                raise ImageUpstreamError(f"网络请求失败: {e}") from e
        finally:
            for handle in opened:
                handle.close()
        return self._parse_json_response(resp)

    async def _post_json(self, url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        try:
            async with self._client(timeout) as client:
                resp = await client.post(url, headers=self._json_headers(), json=payload)
        except httpx.HTTPError as e:
            raise ImageUpstreamError(f"网络请求失败: {e}") from e
        return self._parse_json_response(resp)

    @staticmethod
    def _parse_json_response(resp: httpx.Response) -> dict[str, Any]:
        if resp.status_code != 200:
            raise ImageUpstreamError(describe_http_error(resp), status_code=resp.status_code)
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

    @classmethod
    def parse_generation_response(cls, payload: dict[str, Any]) -> ImageGenerationResult:
        """上游响应体 → 图片线索集合（同时兼容 ``data[].url`` 与 ``data[].b64_json``）。

        兼容面（设计 D4）：请求时给了 ``response_format: "url"``，但上游仍可能回
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
            artifacts.append(
                ImageArtifact(
                    url=str(url) if url else None,
                    b64_json=str(b64_json) if b64_json else None,
                    size=str(item["size"]) if item.get("size") else None,
                )
            )

        if not artifacts:
            raise ImageUpstreamError(f"上游未返回可用图片: {'；'.join(errors) or '原因未知'}")

        usage = payload.get("usage")
        return ImageGenerationResult(
            artifacts=artifacts,
            usage=usage if isinstance(usage, dict) else None,
            raw=payload,
            errors=errors,
        )

    async def generate(
        self,
        *,
        model: str,
        prompt: str,
        size: str | None = None,
        output_format: str | None = None,
        watermark: bool | None = None,
        reference_images: list[str] | None = None,
        extra_params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> ImageGenerationResult:
        """发起出图请求并解析响应（调用方的入口方法）。"""
        payload = await self.request_generation(
            model=model,
            prompt=prompt,
            size=size,
            output_format=output_format,
            watermark=watermark,
            reference_images=reference_images,
            extra_params=extra_params,
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
            raise ImageUpstreamError(f"下载上游图片失败: HTTP {resp.status_code}: {_snippet(resp.content)}", status_code=resp.status_code)
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
]
