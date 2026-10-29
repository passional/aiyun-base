"""SSH 运维服务测试：凭据加密、认证方式校验、私钥解析、批量执行容错。"""

import io
from types import SimpleNamespace
from typing import Any

import paramiko
import pytest

from app.core.exceptions import CustomException
from app.modules.ops.ssh import service as service_module
from app.modules.ops.ssh.schema import (
    BatchExecResultSchema,
    SshHostCreateSchema,
    SshHostOutSchema,
    SshHostUpdateSchema,
    SshSnippetCreateSchema,
)
from app.modules.ops.ssh.service import SshHostService, SshSnippetService, _load_private_key
from app.utils.crypto_util import CryptoUtil


class _FakeCrud:
    def __init__(self, rows: list[SimpleNamespace] | None = None) -> None:
        self.rows = rows or []
        self.created: list[dict[str, Any]] = []
        self.updated: list[dict[str, Any]] = []
        self.deleted: list[int] = []

    async def get(self, **kwargs: Any) -> SimpleNamespace | None:
        name = kwargs.get("name")
        return next((row for row in self.rows if row.name == name), None)

    async def get_or_404(self, id: int, msg: str) -> SimpleNamespace:
        for row in self.rows:
            if row.id == id:
                return row
        raise CustomException(msg=msg)

    async def get_list(self) -> list[SimpleNamespace]:
        return self.rows

    async def create(self, data: dict[str, Any]) -> SimpleNamespace:
        self.created.append(data)
        return _row_ns(**data)

    async def update(self, id: int, data: dict[str, Any]) -> SimpleNamespace:
        self.updated.append(data)
        return _row_ns(id=id, **data)

    async def delete(self, ids: list[int]) -> None:
        self.deleted.extend(ids)


def _host_service(crud: _FakeCrud) -> SshHostService:
    service = SshHostService.__new__(SshHostService)
    setattr(service, "auth", SimpleNamespace(user=SimpleNamespace(id=1, username="admin")))
    setattr(service, "db", None)
    setattr(service, "_crud", lambda: crud)
    return service


def _row_ns(**overrides: Any) -> SimpleNamespace:
    """构造带全部凭据字段的行（避免 _to_out 访问缺失字段）。"""
    base: dict[str, Any] = {
        "id": 1,
        "name": "",
        "host": "",
        "port": 22,
        "username": "",
        "auth_type": "password",
        "password": None,
        "private_key": None,
        "passphrase": None,
        "status": 0,
        "description": "",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _host_row(**overrides: Any) -> SimpleNamespace:
    data: dict[str, Any] = {
        "id": 1,
        "name": "web-1",
        "host": "127.0.0.1",
        "port": 22,
        "username": "root",
        "auth_type": "password",
        "password": CryptoUtil.encrypt("secret"),
        "private_key": None,
        "passphrase": None,
        "status": 0,
        "description": "",
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def test_host_out_schema_has_no_credentials() -> None:
    fields = set(SshHostOutSchema.model_fields)
    assert "password" not in fields
    assert "private_key" not in fields
    assert "passphrase" not in fields


async def test_create_password_host_encrypts_password() -> None:
    crud = _FakeCrud()
    service = _host_service(crud)
    data = SshHostCreateSchema(name="web-1", host="10.0.0.1", port=22, username="root", auth_type="password", password="secret")
    out = await service.create(data)
    assert crud.created and crud.created[0]["password"] != "secret"
    assert CryptoUtil.decrypt(crud.created[0]["password"]) == "secret"
    assert out.has_password is True and out.has_private_key is False


async def test_create_password_host_requires_password() -> None:
    service = _host_service(_FakeCrud())
    data = SshHostCreateSchema(name="w", host="h", username="root", auth_type="password")
    with pytest.raises(CustomException, match="必须提供密码"):
        await service.create(data)


async def test_create_key_host_requires_private_key() -> None:
    service = _host_service(_FakeCrud())
    data = SshHostCreateSchema(name="w", host="h", username="root", auth_type="key")
    with pytest.raises(CustomException, match="必须提供私钥"):
        await service.create(data)


async def test_update_without_credentials_keeps_them() -> None:
    crud = _FakeCrud([_host_row()])
    service = _host_service(crud)
    data = SshHostUpdateSchema(name="web-1", host="10.0.0.2", port=22, username="root", auth_type="password")
    await service.update(1, data)
    assert "password" not in crud.updated[0] and "private_key" not in crud.updated[0]


async def test_duplicate_name_rejected() -> None:
    crud = _FakeCrud([_host_row()])
    service = _host_service(crud)
    data = SshHostCreateSchema(name="web-1", host="h", username="u", password="p")
    with pytest.raises(CustomException, match="名称已存在"):
        await service.create(data)


def test_load_private_key_parses_generated_rsa() -> None:
    key = paramiko.RSAKey.generate(2048)
    buffer = io.StringIO()
    key.write_private_key(buffer)
    parsed = _load_private_key(buffer.getvalue(), None)
    assert parsed is not None


def test_load_private_key_rejects_garbage() -> None:
    with pytest.raises(CustomException, match="无法解析私钥"):
        _load_private_key("not-a-key", None)


async def test_test_connection_uses_injected_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    class _Client:
        def close(self) -> None:
            calls.append({"closed": True})

    def _fake_connect(**kwargs: Any) -> _Client:
        calls.append(kwargs)
        return _Client()

    monkeypatch.setattr(service_module, "connect_client", _fake_connect)
    service = _host_service(_FakeCrud())
    from app.modules.ops.ssh.schema import SshHostTestSchema

    ok = await service.test_connection(
        SshHostTestSchema(host="h", port=22, username="u", auth_type="password", password="p")
    )
    assert ok is True
    assert calls and calls[0]["password"] == "p" and calls[-1] == {"closed": True}


async def test_test_connection_failure_is_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(**kwargs: Any) -> Any:
        raise OSError("connection refused")

    monkeypatch.setattr(service_module, "connect_client", _boom)
    service = _host_service(_FakeCrud())
    from app.modules.ops.ssh.schema import SshHostTestSchema

    with pytest.raises(CustomException, match="连接失败"):
        await service.test_connection(
            SshHostTestSchema(host="h", port=22, username="u", auth_type="password", password="p")
        )


async def test_batch_execute_reports_missing_and_disabled_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    crud = _FakeCrud([_host_row(id=1), _host_row(id=2, name="disabled", status=1)])
    service = _host_service(crud)

    def _fake_exec(row: SimpleNamespace, command: str, timeout: int) -> BatchExecResultSchema:
        return BatchExecResultSchema(host_id=row.id, host_name=row.name, ok=True, exit_status=0, stdout="ok")

    monkeypatch.setattr(service, "_exec_on_host", _fake_exec)
    results = await service.batch_execute([1, 2, 999], "uptime", 5)
    by_id = {item.host_id: item for item in results}
    assert by_id[1].ok is True and by_id[1].stdout == "ok"
    assert by_id[2].error == "主机已停用"
    assert by_id[999].error == "主机不存在"


async def test_snippet_create_returns_validated_row() -> None:
    crud = _FakeCrud()
    service = SshSnippetService.__new__(SshSnippetService)
    setattr(service, "auth", SimpleNamespace(user=SimpleNamespace(id=1, username="admin")))
    setattr(service, "db", None)
    setattr(service, "_crud", lambda: crud)
    row = await service.create(SshSnippetCreateSchema(name="disk", command="df -h"))
    assert row.name == "disk" and row.command == "df -h"
