"""指定渠道（pin）准备服务测试（任务 3.2）与日志字段解析测试（任务 3.3）

覆盖：每次运行专用评估令牌的「先查后建、幂等、明文密钥取回、加密入库」，
以及远端孤儿令牌复用；日志字段解析与降级。
"""

import json
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.modules.channel_eval.platform.model import ChannelEvalPlatformModel
from app.modules.channel_eval.run.log_parser import parse_log_record
from app.modules.channel_eval.run.model import ChannelEvalRunModel
from app.modules.channel_eval.run.new_api_client import NewApiClient
from app.modules.channel_eval.run.provision import (
    ProvisionError,
    ensure_eval_token,
    eval_run_token_name,
    resolve_eval_token_key,
)
from app.utils.crypto_util import CryptoUtil


class FakeProvisionClient:
    """new-api 管理端假客户端（记录写调用次数，验证幂等）"""

    def __init__(self, tokens: list[dict]) -> None:
        self.tokens = tokens
        self.create_calls: list[tuple[str, str]] = []
        self.list_calls = 0

    async def list_tokens(self) -> list[dict]:
        self.list_calls += 1
        return [dict(t) for t in self.tokens]

    async def create_eval_token(self, name: str, group: str) -> dict:
        self.create_calls.append((name, group))
        token = {"id": 99, "name": name, "key": f"sk-{name}-realkey0123456789abcdef", "group": group}
        self.tokens.append(token)
        return token

    async def get_token_key(self, token_id: int) -> str:
        token = next((t for t in self.tokens if t["id"] == token_id), None)
        if token is None:
            raise ProvisionError("令牌不存在")
        return str(token["key"])


class FakeHttpNewApi:
    """内存版 new-api 假服务（HTTP 层）：复刻线上实测的令牌密钥契约。

    - 渠道列表不回传密钥（与线上 ``Omit("key")`` 一致）；
    - 令牌列表只回脱敏 key，明文经 ``POST /api/token/{id}/key`` 返回（与线上实测一致）；
    - 创建令牌返回 success=true 但不回明文（部分版本行为），明文必须经密钥接口取回。
    """

    def __init__(self) -> None:
        self.tokens: list[dict] = []
        self.real_keys: dict[int, str] = {}
        self._next_token_id = 99

    @staticmethod
    def _mask(key: str) -> str:
        """复刻 new-api MaskTokenKey：列表/查询接口只回脱敏值。"""
        if len(key) <= 4:
            return "*" * len(key)
        if len(key) <= 8:
            return key[:2] + "****" + key[-2:]
        return key[:4] + "**********" + key[-4:]

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/api/user/self" in url:
            return httpx.Response(200, json={"success": True, "data": {"id": 1, "username": "root"}})
        if request.method == "POST" and "/api/token/" in url and url.endswith("/key"):
            token_id = int(url.rstrip("/").split("/")[-2])
            key = self.real_keys.get(token_id)
            if key is None:
                return httpx.Response(200, json={"success": False, "message": "record not found"})
            return httpx.Response(200, json={"success": True, "data": {"key": key}})
        if request.method == "POST" and "/api/token/" in url:
            body = json.loads(request.content)
            token_id = self._next_token_id
            self._next_token_id += 1
            real = f"sk-{body['name']}-realkey0123456789abcdef"
            self.real_keys[token_id] = real
            self.tokens.append({"id": token_id, "name": body["name"], "key": self._mask(real), "group": body["group"]})
            return httpx.Response(200, json={"success": True, "message": "", "data": True})
        if "/api/token/" in url:
            return httpx.Response(200, json={"success": True, "data": {"items": [dict(t) for t in self.tokens], "total": len(self.tokens)}})
        raise AssertionError(f"unexpected request: {request.method} {url}")


def _install_http(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    """把 NewApiClient 的 HTTP 出口替换为 MockTransport（真实客户端代码走完整请求链）。"""

    def _client(self, timeout=None):
        return httpx.AsyncClient(timeout=timeout, transport=httpx.MockTransport(handler), trust_env=False)

    monkeypatch.setattr(NewApiClient, "_client", _client)


async def _make_platform(db_maker: async_sessionmaker, *, admin_key_enc: str | None = None) -> ChannelEvalPlatformModel:
    if admin_key_enc is None:
        admin_key_enc = CryptoUtil.encrypt("sk-admin")
    async with db_maker() as db, db.begin():
        platform = ChannelEvalPlatformModel(
            name="apiworld",
            type="admin",
            base_url="https://api.example.com",
            test_key_enc=CryptoUtil.encrypt("sk-test"),
            admin_key_enc=admin_key_enc,
        )
        db.add(platform)
        await db.flush()
        db_id = platform.id
    async with db_maker() as db:
        return await db.get(ChannelEvalPlatformModel, db_id)


async def _make_run(db_maker: async_sessionmaker, platform: ChannelEvalPlatformModel) -> ChannelEvalRunModel:
    async with db_maker() as db, db.begin():
        run = ChannelEvalRunModel(platform_id=platform.id, status="pending", params_json={})
        db.add(run)
        await db.flush()
        run_id = run.id
    async with db_maker() as db:
        return await db.get(ChannelEvalRunModel, run_id)


# ─────────────────────── 3.2 评估令牌（指定渠道 pin） ───────────────────────


async def test_ensure_eval_token_first_time_and_idempotent(eval_db: async_sessionmaker, monkeypatch: pytest.MonkeyPatch) -> None:
    """首次：创建专用评估令牌并加密入库；重复执行：复用既有令牌，不重复创建。"""
    platform = await _make_platform(eval_db)
    run = await _make_run(eval_db, platform)
    fake = FakeProvisionClient(tokens=[])
    with patch("app.modules.channel_eval.run.provision.NewApiClient", return_value=fake):
        # 首次执行：创建 eval-run{运行ID}（group=default），明文 key 立即加密写回 run 行
        async with eval_db() as db, db.begin():
            run_row = await db.get(ChannelEvalRunModel, run.id)
            key = await ensure_eval_token(db, platform, run_row)
        assert key == "sk-eval-run%d-realkey0123456789abcdef" % run.id
        assert "*" not in key
        assert fake.create_calls == [(f"eval-run{run.id}", "default")]

        async with eval_db() as db:
            run_row = await db.get(ChannelEvalRunModel, run.id)
            assert run_row.eval_token_id == 99
            assert CryptoUtil.decrypt(run_row.eval_token_key_enc) == key

        # 重复执行：远端令牌已存在 → 复用，不重复创建
        async with eval_db() as db, db.begin():
            run_row = await db.get(ChannelEvalRunModel, run.id)
            key2 = await ensure_eval_token(db, platform, run_row)
        assert key2 == key
        assert fake.create_calls == [(f"eval-run{run.id}", "default")]  # 无新增写
        assert fake.list_calls == 2  # 先查后建：每次都查

        # resolve 路径：已登记明文 → 直接解密返回，不再访问远端
        async with eval_db() as db, db.begin():
            run_row = await db.get(ChannelEvalRunModel, run.id)
            key3 = await resolve_eval_token_key(db, platform, run_row)
        assert key3 == key
        assert fake.list_calls == 2  # resolve 未再触发远端查询


async def test_ensure_eval_token_http_contract_masked_key(eval_db: async_sessionmaker, monkeypatch: pytest.MonkeyPatch) -> None:
    """回归（apiworld 联调）：列表只回脱敏 key，创建后明文必须经密钥接口取回。

    若把列表的脱敏值（含 ``*``）当密钥发请求会 401 Invalid token；本用例断言
    创建与复用两条路径返回/入库的都是明文。
    """
    platform = await _make_platform(eval_db)
    run = await _make_run(eval_db, platform)
    fake = FakeHttpNewApi()
    _install_http(monkeypatch, fake.handler)

    async with eval_db() as db, db.begin():
        run_row = await db.get(ChannelEvalRunModel, run.id)
        key = await ensure_eval_token(db, platform, run_row)

    real_key = fake.real_keys[99]
    assert key == real_key and "*" not in key  # 必须是明文，不能是列表的脱敏值
    assert [t["name"] for t in fake.tokens] == [f"eval-run{run.id}"]
    assert fake.tokens[0]["group"] == "default"
    async with eval_db() as db:
        run_row = await db.get(ChannelEvalRunModel, run.id)
        assert CryptoUtil.decrypt(run_row.eval_token_key_enc) == real_key

    # 复用路径：即使 run 行密文被清掉（模拟重建），远端按名存在 → 复用并重新取明文
    async with eval_db() as db, db.begin():
        run_row = await db.get(ChannelEvalRunModel, run.id)
        run_row.eval_token_key_enc = None
        run_row.eval_token_id = None
        key2 = await ensure_eval_token(db, platform, run_row)
    assert key2 == real_key and "*" not in key2
    assert len(fake.tokens) == 1  # 未重复创建


async def test_resolve_eval_token_key_reuses_stored_without_remote(eval_db: async_sessionmaker, monkeypatch: pytest.MonkeyPatch) -> None:
    """resolve 兜底：run 行已登记明文 → 解密返回，不访问远端（引擎热路径）。"""
    platform = await _make_platform(eval_db)
    run = await _make_run(eval_db, platform)
    stored = "sk-eval-run7-realkey0123456789abcdef"
    async with eval_db() as db, db.begin():
        run_row = await db.get(ChannelEvalRunModel, run.id)
        run_row.eval_token_id = 42
        run_row.eval_token_key_enc = CryptoUtil.encrypt(stored)

    fake = FakeProvisionClient(tokens=[])
    with patch("app.modules.channel_eval.run.provision.NewApiClient", return_value=fake):
        async with eval_db() as db, db.begin():
            run_row = await db.get(ChannelEvalRunModel, run.id)
            key = await resolve_eval_token_key(db, platform, run_row)
    assert key == stored
    assert fake.list_calls == 0  # 无远端访问


async def test_ensure_eval_token_reuses_orphan_remote_token(eval_db: async_sessionmaker, monkeypatch: pytest.MonkeyPatch) -> None:
    """远端已存在同名令牌（上次准备中断的孤儿）→ 复用，不重复创建。"""
    platform = await _make_platform(eval_db)
    run = await _make_run(eval_db, platform)
    fake = FakeProvisionClient(
        tokens=[{"id": 77, "name": f"eval-run{run.id}", "key": "sk-orphan-realkey0123456789abcdef", "group": "default"}]
    )
    with patch("app.modules.channel_eval.run.provision.NewApiClient", return_value=fake):
        async with eval_db() as db, db.begin():
            run_row = await db.get(ChannelEvalRunModel, run.id)
            key = await ensure_eval_token(db, platform, run_row)
    assert key == "sk-orphan-realkey0123456789abcdef"
    assert fake.create_calls == []  # 不新建
    async with eval_db() as db:
        run_row = await db.get(ChannelEvalRunModel, run.id)
        assert run_row.eval_token_id == 77


async def test_eval_run_token_name_convention() -> None:
    """命名约定：eval-run{运行ID}。"""
    assert eval_run_token_name(3) == "eval-run3"
    assert eval_run_token_name(0) == "eval-run0"


# ─────────────────────── 3.3 日志字段解析与降级 ───────────────────────


def test_parse_log_record_full_payload() -> None:
    """全字段载荷：use_time 秒→毫秒、other.frt、request_id、tokens、quota、渠道。"""
    record: dict[str, Any] = {
        "id": 1,
        "created_at": 1700000000,
        "type": 2,
        "token_name": "eval-run3",
        "model_name": "claude-opus-5",
        "channel_id": 70,
        "prompt_tokens": 12,
        "completion_tokens": 34,
        "use_time": 2,
        "quota": 1700,
        "other": '{"frt": 1500, "request_id": "req-abc"}',
    }
    parsed = parse_log_record(record)
    assert parsed["request_id"] == "req-abc"
    assert parsed["frt_ms"] == 1500
    assert parsed["use_time_ms"] == 2000  # 秒 → 毫秒
    assert parsed["tokens_in"] == 12
    assert parsed["tokens_out"] == 34
    assert parsed["cost"] == 1700
    assert parsed["channel_id"] == 70
    assert parsed["model_name"] == "claude-opus-5"


def test_parse_log_record_missing_fields() -> None:
    """缺字段降级：frt/request_id/use_time 缺失时对应字段置空，不凭空填值。"""
    parsed = parse_log_record({"id": 2, "model_name": "m", "prompt_tokens": 5})
    assert parsed["request_id"] is None
    assert parsed["frt_ms"] is None
    assert parsed["use_time_ms"] is None
    assert parsed["tokens_in"] == 5
    assert parsed["tokens_out"] is None
    assert parsed["channel_id"] is None


def test_parse_log_record_other_variants() -> None:
    """other 为 dict / 非法 JSON / request_id 在顶层时均可解析。"""
    assert parse_log_record({"other": {"frt": 300}})["frt_ms"] == 300
    assert parse_log_record({"other": "not-json"})["frt_ms"] is None
    assert parse_log_record({"request_id": "top-level"})["request_id"] == "top-level"
    # channel 字段名兼容
    assert parse_log_record({"channel": 88})["channel_id"] == 88
