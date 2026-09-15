"""new-api 集成层（ADR-0001 白名单强约束）

本文件是评估系统对 new-api 站点的**唯一**HTTP 出口，接口面即白名单：

- 只读方法：``list_channels`` / ``list_tokens`` / ``list_logs`` / ``list_self_logs``
  / ``list_models`` / ``get_admin_user`` / ``get_token_key``
  / ``test_admin_connection`` / ``test_user_connection`` / ``probe_pin_capability``
- 写方法**仅有一个**（ADR-0001）：``create_eval_token``（创建每次运行的专用评估令牌）。

指定渠道（pin）不走任何写操作：请求头 ``Bearer <管理员令牌>-<渠道ID>`` 即把请求
pin 到指定渠道（new-api v1.0.0-rc.21+ 原生，见 provision.py）。

系统 MUST NOT 在此文件之外直接调用 new-api 管理接口；MUST NOT 修改渠道的优先级、
权重、启停状态、模型列表或模型映射，MUST NOT 删除渠道或令牌。代码评审锚点即本文件。
"""

import asyncio
from typing import Any

import httpx

from app.core.logger import logger

# 分页拉全的安全上限：防远端返回异常 total 时无限循环
_MAX_PAGES = 200
_PAGE_SIZE = 100
# 单次管理调用超时（秒）；聊天流式请求的超时由运行参数控制，不经此值
_ADMIN_TIMEOUT = 15.0


class NewApiError(Exception):
    """new-api 调用失败（网络/HTTP/业务 success=false）"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _first_error_snippet(body: bytes, limit: int = 200) -> str:
    try:
        text = body.decode("utf-8", errors="replace").strip()
    except Exception:  # noqa: BLE001
        text = ""
    return text[:limit] or "(空响应体)"


class NewApiClient:
    """new-api 站点客户端（httpx.AsyncClient 按请求创建，无连接泄漏）"""

    def __init__(
        self,
        base_url: str,
        *,
        admin_key: str | None = None,
        test_key: str | None = None,
        admin_user_id: str | None = None,
        timeout: float = _ADMIN_TIMEOUT,
    ) -> None:
        self._base_url = (base_url or "").rstrip("/")
        self._admin_key = (admin_key or "").strip() or None
        self._test_key = (test_key or "").strip() or None
        self._timeout = timeout
        # new-api 较新版本要求管理请求携带 New-Api-User 头（登录用户ID）：
        # 优先使用平台登记的管理用户ID（强制校验的站点无法靠探测发现）；
        # 缺省时经只读接口 /api/user/self 探测并缓存，探测失败则不带（兼容旧版）
        self._admin_user_id: str | None = (admin_user_id or "").strip() or None

    # ─────────────────────────── 基础请求 ───────────────────────────

    def _client(self, timeout: float | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout or self._timeout, trust_env=False)

    def _admin_headers(self) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._admin_key}"}
        if self._admin_user_id:
            headers["New-Api-User"] = self._admin_user_id
        return headers

    @staticmethod
    def _admin_error_message(status_code: int, body: bytes, *, hint_new_api_user: bool) -> str:
        """把 HTTP 非 200 响应体转成可读错误，命中 New-Api-User 强制校验时追加引导。"""
        text = _first_error_snippet(body)
        if status_code == 401 and "New-Api-User" in text and hint_new_api_user:
            text += "（该 new-api 站点要求管理请求携带 New-Api-User 头：请在平台登记「管理用户ID」填写登录用户ID）"
        return text

    def _user_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._test_key}"}

    async def _ensure_admin_user_id(self) -> None:
        """解析管理凭证对应的用户ID（New-Api-User 头），失败静默跳过。

        已显式提供 admin_user_id 时跳过探测（强制校验的站点无头请求必然失败）。
        """
        if self._admin_user_id or not self._admin_key:
            return
        try:
            async with self._client() as client:
                resp = await client.get(
                    f"{self._base_url}/api/user/self",
                    headers={"Authorization": f"Bearer {self._admin_key}"},
                )
            if resp.status_code == 200:
                payload = resp.json()
                data = payload.get("data") or {}
                user_id = data.get("id")
                if user_id is not None:
                    self._admin_user_id = str(user_id)
        except Exception as e:  # noqa: BLE001
            logger.debug("解析 new-api 管理用户ID失败（继续不带 New-Api-User 头）: {}", e)

    async def _admin_get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """管理端 GET：校验 HTTP 状态与业务 success，返回 data 字段。"""
        await self._ensure_admin_user_id()
        try:
            async with self._client() as client:
                resp = await client.get(f"{self._base_url}{path}", params=params, headers=self._admin_headers())
        except httpx.HTTPError as e:
            raise NewApiError(f"网络请求失败: {e}") from e
        if resp.status_code != 200:
            raise NewApiError(
                f"HTTP {resp.status_code}: {self._admin_error_message(resp.status_code, resp.content, hint_new_api_user=not self._admin_user_id)}"
            )
        payload = resp.json()
        if payload.get("success") is not True:
            raise NewApiError(f"接口返回失败: {payload.get('message') or payload}")
        return payload.get("data")

    async def _admin_post_json(self, path: str, body: dict[str, Any]) -> Any:
        """管理端 POST JSON：校验 HTTP 状态与业务 success，返回 data 字段。"""
        await self._ensure_admin_user_id()
        try:
            async with self._client() as client:
                resp = await client.post(f"{self._base_url}{path}", json=body, headers=self._admin_headers())
        except httpx.HTTPError as e:
            raise NewApiError(f"网络请求失败: {e}") from e
        if resp.status_code != 200:
            raise NewApiError(
                f"HTTP {resp.status_code}: {self._admin_error_message(resp.status_code, resp.content, hint_new_api_user=not self._admin_user_id)}"
            )
        payload = resp.json()
        if payload.get("success") is not True:
            raise NewApiError(f"接口返回失败: {payload.get('message') or payload}")
        return payload.get("data")

    async def _user_get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """用户级 GET（测试凭证）：校验 HTTP 状态与业务 success，返回 data 字段。"""
        try:
            async with self._client() as client:
                resp = await client.get(f"{self._base_url}{path}", params=params, headers=self._user_headers())
        except httpx.HTTPError as e:
            raise NewApiError(f"网络请求失败: {e}") from e
        if resp.status_code != 200:
            raise NewApiError(f"HTTP {resp.status_code}: {_first_error_snippet(resp.content)}")
        payload = resp.json()
        if payload.get("success") is not True:
            raise NewApiError(f"接口返回失败: {payload.get('message') or payload}")
        return payload.get("data")

    @staticmethod
    def _extract_items(data: Any) -> tuple[list[Any], int | None]:
        """兼容 new-api 分页返回的两种形态：data=[...]（旧版，无全局 total）或 data={items,total}（新版）。"""
        if isinstance(data, list):
            return data, None
        if isinstance(data, dict):
            items = data.get("items")
            if isinstance(items, list):
                total = data.get("total")
                return items, int(total) if isinstance(total, (int, float)) else None
        return [], None

    async def _paginate(self, fetch_page: Any, extra_params: dict[str, Any] | None = None) -> list[dict]:
        """分页拉全：dict 形态按 total 判断拉齐；list 形态无全局 total，靠页大小判断。"""
        result: list[dict] = []
        for page in range(1, _MAX_PAGES + 1):
            params: dict[str, Any] = {"p": page, "page_size": _PAGE_SIZE, "size": _PAGE_SIZE}
            if extra_params:
                params.update(extra_params)
            data = await fetch_page(params)
            items, total = self._extract_items(data)
            result.extend(item for item in items if isinstance(item, dict))
            if not items or len(items) < _PAGE_SIZE or (total is not None and len(result) >= total):
                break
        return result

    # ─────────────────────────── 只读方法 ───────────────────────────

    async def list_channels(self) -> list[dict]:
        """拉取全部渠道目录（编号/名称/状态/分组/优先级/权重/模型列表）。"""
        return await self._paginate(lambda params: self._admin_get("/api/channel/", params=params))

    async def list_tokens(self) -> list[dict]:
        """拉取全部令牌（用于「先查后建」幂等检查）。"""
        return await self._paginate(lambda params: self._admin_get("/api/token/", params=params))

    async def list_logs(self, token_name: str, start_ts: int, end_ts: int) -> list[dict]:
        """管理级日志：按令牌名 + 时间窗分页拉全（消耗日志 type=0 由调用方过滤）。"""
        return await self._paginate(
            lambda params: self._admin_get("/api/log/", params=params),
            extra_params={
                "type": 0,
                "token_name": token_name,
                "start_timestamp": start_ts,
                "end_timestamp": end_ts,
            },
        )

    async def list_self_logs(self, start_ts: int, end_ts: int) -> list[dict]:
        """用户级日志：以测试凭证（用户令牌）拉取本人日志（基准渠道用）。"""
        return await self._paginate(
            lambda params: self._user_get("/api/log/self", params=params),
            extra_params={
                "type": 0,
                "start_timestamp": start_ts,
                "end_timestamp": end_ts,
            },
        )

    async def list_models(self) -> list[str]:
        """以测试凭证调用 OpenAI 兼容模型列表（用户级只读）。"""
        try:
            async with self._client() as client:
                resp = await client.get(f"{self._base_url}/v1/models", headers=self._user_headers())
        except httpx.HTTPError as e:
            raise NewApiError(f"网络请求失败: {e}") from e
        if resp.status_code != 200:
            raise NewApiError(f"HTTP {resp.status_code}: {_first_error_snippet(resp.content)}")
        payload = resp.json()
        models: list[str] = []
        for item in payload.get("data") or []:
            if isinstance(item, dict) and item.get("id"):
                models.append(str(item["id"]))
        return sorted(set(models))

    async def get_token_key(self, token_id: int) -> str:
        """取回令牌明文 key（列表/查询接口只回脱敏值，脱敏值发请求会 401 Invalid token）。"""
        if token_id <= 0:
            raise NewApiError(f"令牌 #{token_id} id 无效，无法取回明文密钥")
        try:
            data = await self._admin_post_json(f"/api/token/{token_id}/key", {})
        except NewApiError as e:
            raise NewApiError(f"取回令牌 #{token_id} 明文密钥失败: {e.message}") from e
        key = str((data or {}).get("key") or "") if isinstance(data, dict) else str(data or "")
        if not key or "*" in key:
            raise NewApiError(f"令牌 #{token_id} 明文密钥取回失败（返回为空或仍为脱敏值）")
        return key

    async def get_admin_user(self) -> dict | None:
        """管理凭证自画像（只读探测）。"""
        try:
            async with self._client() as client:
                resp = await client.get(f"{self._base_url}/api/user/self", headers=self._admin_headers())
        except httpx.HTTPError as e:
            raise NewApiError(f"网络请求失败: {e}") from e
        if resp.status_code != 200:
            raise NewApiError(
                f"HTTP {resp.status_code}: {self._admin_error_message(resp.status_code, resp.content, hint_new_api_user=not self._admin_user_id)}"
            )
        payload = resp.json()
        if payload.get("success") is not True:
            raise NewApiError(f"管理凭证无效: {payload.get('message') or payload}")
        data = payload.get("data")
        return data if isinstance(data, dict) else None

    # ─────────────────────────── 写方法（白名单仅一项，ADR-0001） ───────────────────────────

    async def create_eval_token(self, name: str, group: str) -> dict:
        """创建评估专用令牌（每次运行 1 个，属平台管理用户，返回 new-api 侧记录）。

        ``key`` 取回：新版 new-api 列表/查询接口只回脱敏值（``key[:4]+**********+key[-4:]``），
        脱敏值发请求会 401 Invalid token；创建响应未带明文 key 时经
        ``POST /api/token/{id}/key`` 取回。
        """
        body = {
            "name": name,
            "remain_quota": 500000,  # 令牌额度建议由管理员在 new-api 侧按需调整
            "expired_time": -1,
            "unlimited_quota": False,
            "model_limits_enabled": False,
            "model_limits": "",
            "allow_ips": "",
            "group": group,
        }
        created = await self._admin_post_json("/api/token/", body=body)
        # new-api 创建接口部分版本不回传记录，重新按名查询取回
        tokens = await self.list_tokens()
        token = next((t for t in tokens if t.get("name") == name), None)
        if token is None:
            raise NewApiError(f"令牌 {name} 创建后查询不到")
        created_key = str(created.get("key") or "") if isinstance(created, dict) else ""
        if created_key and "*" not in created_key:
            token["key"] = created_key  # 部分版本创建响应直接回明文
        else:
            token["key"] = await self.get_token_key(int(token.get("id") or 0))
        return token


async def probe_pin_capability(base_url: str, token_key: str) -> tuple[bool, str]:
    """探测「指定渠道」（pin）能力：用该令牌发一次 pin 到不存在渠道的请求。

    响应分类（对照 new-api v1.0.0-rc.21 源码核实）：

    - HTTP 400 「无效的渠道 Id」→ pin 机制可用（管理员校验通过，渠道不存在即 400，
      无任何上游调用与消耗）；
    - HTTP 403 「普通用户不支持指定渠道」→ 令牌所属用户不是管理员 → pin 不可用；
    - HTTP 401 → 令牌无效，或该 new-api 版本不支持指定渠道；
    - 其它 → 返回响应摘要。

    探测渠道号固定取 ``999999``（远大于真实渠道数，确保不存在，避免误伤真实渠道）。
    """
    probe_channel_id = 999999
    payload = {
        "model": "__channel_eval_pin_probe__",
        "messages": [{"role": "user", "content": "ping"}],
        "stream": False,
    }
    headers = {
        "Authorization": f"Bearer {token_key}-{probe_channel_id}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=_ADMIN_TIMEOUT, trust_env=False) as client:
            resp = await client.post(f"{base_url.rstrip('/')}/v1/chat/completions", headers=headers, json=payload)
    except httpx.HTTPError as e:
        return False, f"指定渠道探测请求失败: {e}"
    body = resp.text[:300]
    if resp.status_code == 400 and ("渠道" in body or "channel" in body.lower()):
        return True, "指定渠道（pin）可用：管理员令牌校验与渠道直取均生效"
    if resp.status_code == 403 and "普通用户" in body:
        return False, "指定渠道不可用：该平台管理凭证对应的是普通用户，pin 需要管理员用户名下的令牌（普通用户不支持指定渠道）"
    if resp.status_code == 401:
        return False, f"指定渠道探测返回 401：令牌无效，或该 new-api 版本不支持指定渠道（{body}）"
    return False, f"指定渠道探测返回意外结果 HTTP {resp.status_code}: {body}"


async def test_admin_connection(base_url: str, admin_key: str, admin_user_id: str | None = None) -> tuple[bool, str]:
    """管理凭证连通性探测（只读：/api/user/self）。

    ``admin_user_id`` 为 new-api 强制 New-Api-User 头时在平台登记的管理用户ID。
    """
    client = NewApiClient(base_url, admin_key=admin_key, admin_user_id=admin_user_id)
    try:
        user = await asyncio.wait_for(client.get_admin_user(), timeout=20)
        if user:
            return True, f"管理凭证有效（用户: {user.get('username') or user.get('id')}）"
        return True, "管理凭证有效"
    except NewApiError as e:
        return False, e.message
    except TimeoutError:
        return False, "连接超时"


async def test_user_connection(base_url: str, test_key: str) -> tuple[bool, str]:
    """测试凭证连通性探测（只读：/v1/models）。"""
    client = NewApiClient(base_url, test_key=test_key)
    try:
        models = await asyncio.wait_for(client.list_models(), timeout=20)
        return True, f"测试凭证有效（可用模型 {len(models)} 个）"
    except NewApiError as e:
        return False, e.message
    except TimeoutError:
        return False, "连接超时"
