"""存储源配置导出/导入回归测试。

不变量：
1. 导出**绝不含凭据**（password / advanced_config 都不出现）；
2. 导入按名称去重，已存在则跳过、绝不覆盖；
3. 协议非法 / 结构非法 / 端口缺失都有兜底，不会写坏库。

说明：这里用服务层 fake CRUD 验证，不走 HTTP 写路径——测试环境 SQLite 由同步引擎持锁，
API 写请求会等 ~60s 锁超时（测试基础设施问题，与业务逻辑无关）。
"""

import json
from types import SimpleNamespace
from typing import Any

from app.modules.task.storage.node.config_io import EXPORT_FIELDS, build_export, plan_import
from app.modules.task.storage.node.service import StorageNodeService

_BASE_NODE: dict[str, Any] = {
    "name": "n1",
    "protocol": "sftp",
    "host": "127.0.0.1",
    "port": 22,
    "username": "u",
    "bucket": None,
    "endpoint": None,
    "region": None,
    "path_prefix": None,
    "is_secure": False,
    "implicit_tls": False,
    "scheme": "https",
    "encrypt_type": 1,
    "connection_mode": 0,
    "encoding": "UTF-8",
    "multipart_part_size": 50,
    "multipart_concurrency": 6,
    "multipart_memory_budget": 512,
    "status": 0,
    "description": "",
}


def _node(**overrides: Any) -> SimpleNamespace:
    data = dict(_BASE_NODE)
    data.update(overrides)
    # 凭据与高级配置：导出/导入都绝不能出现
    data["password"] = "super-secret"
    data["advanced_config"] = {"proxy": "http://user:pass@proxy"}
    return SimpleNamespace(**data)


class _FakeCrud:
    """记录 create 调用的假 CRUD（get_list 返回既有节点）。"""

    def __init__(self, existing: list[SimpleNamespace]) -> None:
        self.existing = existing
        self.created: list[dict[str, Any]] = []

    async def get_list(self, *args: Any, **kwargs: Any) -> list[SimpleNamespace]:
        return self.existing

    async def create(self, data: dict[str, Any]) -> SimpleNamespace:
        self.created.append(data)
        return SimpleNamespace(id=len(self.created), **data)


def _service(crud: _FakeCrud) -> StorageNodeService:
    service = StorageNodeService.__new__(StorageNodeService)
    setattr(service, "_crud", lambda: crud)
    return service


# ── 纯函数 ────────────────────────────────────────────────────────────


def test_build_export_never_contains_credentials() -> None:
    payload = build_export([_node()])
    node = payload["nodes"][0]
    assert set(node) == set(EXPORT_FIELDS)
    assert "password" not in node
    assert "advanced_config" not in node
    assert "super-secret" not in json.dumps(payload)


def test_plan_import_skips_existing_names() -> None:
    data = {"nodes": [{"name": "a", "protocol": "sftp"}, {"name": "b", "protocol": "s3"}]}
    to_create, skipped, invalid = plan_import(data, {"a"})
    assert [item["name"] for item in to_create] == ["b"]
    assert (skipped, invalid) == (1, 0)


def test_plan_import_rejects_invalid_entries() -> None:
    data = {"nodes": [{"name": "", "protocol": "sftp"}, {"name": "x", "protocol": "ftp2"}, "not-a-dict"]}
    to_create, skipped, invalid = plan_import(data, set())
    assert to_create == []
    assert (skipped, invalid) == (0, 3)


def test_plan_import_defaults_port_by_protocol() -> None:
    data = {"nodes": [{"name": "b", "protocol": "s3"}, {"name": "c", "protocol": "sftp", "port": "abc"}]}
    to_create, _, _ = plan_import(data, set())
    assert to_create[0]["port"] == 443
    assert to_create[1]["port"] == 22


def test_plan_import_never_carries_credentials() -> None:
    """即便导入文件里夹带了 password/advanced_config，也不能写进待创建条目。"""
    data = {
        "nodes": [
            {
                "name": "leaky",
                "protocol": "sftp",
                "password": "super-secret",
                "advanced_config": {"proxy": "x"},
            }
        ]
    }
    to_create, _, _ = plan_import(data, set())
    assert to_create and "password" not in to_create[0]
    assert "advanced_config" not in to_create[0]


# ── 服务层 ────────────────────────────────────────────────────────────


async def test_service_export_omits_credentials() -> None:
    service = _service(_FakeCrud([_node()]))
    payload = await service.export_config()
    assert payload["version"] >= 1
    assert "exported_at" in payload
    for node in payload["nodes"]:
        assert "password" not in node
        assert "advanced_config" not in node


async def test_service_import_dedupes_and_creates_without_credentials() -> None:
    crud = _FakeCrud([_node(name="exists")])
    service = _service(crud)
    payload = {
        "nodes": [
            {"name": "exists", "protocol": "sftp"},
            {"name": "new-node", "protocol": "s3", "region": "us-east-1"},
            {"name": "", "protocol": "sftp"},
        ]
    }
    result = await service.import_config(payload)
    assert result == {"created": 1, "skipped": 1, "invalid": 1}
    assert len(crud.created) == 1
    created = crud.created[0]
    assert created["name"] == "new-node"
    assert created["protocol"] == "s3"
    assert "password" not in created
    assert "advanced_config" not in created
