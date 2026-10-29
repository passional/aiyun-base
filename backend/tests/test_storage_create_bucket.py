"""对象存储建桶（create_bucket）回归测试。

覆盖：服务层通用校验、协议守卫、适配器委托与资源释放；S3 的 LocationConstraint 规则；
OBS 以状态码而非异常报告失败的处理。
"""

from typing import Any

import pytest

from app.core.exceptions import CustomException
from app.modules.task.storage.browse.service import StorageFileService
from app.modules.task.storage.core.base import StorageAdapterConfig, StorageProtocol
from app.modules.task.storage.core.obs_adapter import ObsStorageAdapter
from app.modules.task.storage.core.s3_adapter import S3StorageAdapter


def _service() -> StorageFileService:
    # 只测 create_bucket 的校验/委托逻辑：用 __new__ 跳过需要真实 auth/db 的构造
    return StorageFileService.__new__(StorageFileService)


def _config(protocol: StorageProtocol, **overrides: Any) -> StorageAdapterConfig:
    data: dict[str, Any] = {
        "protocol": protocol,
        "port": 443,
        "endpoint": "https://example.com",
        "bucket": "demo",
        "username": "ak",
        "password": "sk",
    }
    data.update(overrides)
    return StorageAdapterConfig(**data)


class _FakeAdapter:
    """记录委托调用与关闭状态的假适配器。"""

    def __init__(self) -> None:
        self.created: list[str] = []
        self.closed = False

    async def create_bucket(self, bucket: str) -> None:
        self.created.append(bucket)

    async def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize("bad", ["", "   ", "a/b", "a b", "x" * 64])
async def test_create_bucket_rejects_blank_or_invalid_names(bad: str) -> None:
    with pytest.raises(CustomException):
        await _service().create_bucket(None, bad)


async def test_create_bucket_rejects_non_object_protocol(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _service()

    async def _get_source(_source_id: int | None) -> StorageAdapterConfig:
        return _config(StorageProtocol.FTP, port=21, host="127.0.0.1")

    monkeypatch.setattr(service, "_get_source", _get_source)
    with pytest.raises(CustomException, match="仅对象存储协议"):
        await service.create_bucket(None, "bucket-1")


async def test_create_bucket_delegates_and_always_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.modules.task.storage.core.factory import StorageAdapterFactory

    service = _service()
    fake = _FakeAdapter()

    async def _get_source(_source_id: int | None) -> StorageAdapterConfig:
        return _config(StorageProtocol.S3, region="ap-southeast-1")

    monkeypatch.setattr(service, "_get_source", _get_source)
    monkeypatch.setattr(StorageAdapterFactory, "create", staticmethod(lambda _config: fake))

    await service.create_bucket(None, "  new-bucket  ")
    assert fake.created == ["new-bucket"], "桶名应先 strip 再委托给适配器"
    assert fake.closed is True, "无论成功失败都必须关闭适配器"


def test_base_adapter_default_create_bucket_is_unsupported() -> None:
    from app.modules.task.storage.core.local_adapter import LocalStorageAdapter

    adapter = LocalStorageAdapter(StorageAdapterConfig(protocol=StorageProtocol.LOCAL, port=0, host="/tmp"))
    with pytest.raises(NotImplementedError):
        adapter._sync_create_bucket("x")


def test_s3_create_bucket_passes_location_constraint() -> None:
    adapter = S3StorageAdapter(_config(StorageProtocol.S3, region="ap-southeast-1"))
    calls: dict[str, Any] = {}

    class _Client:
        def create_bucket(self, **kwargs: Any) -> None:
            calls.update(kwargs)

    adapter.client = _Client()  # type: ignore[assignment]
    adapter._sync_create_bucket("new-bucket")
    assert calls == {
        "Bucket": "new-bucket",
        "CreateBucketConfiguration": {"LocationConstraint": "ap-southeast-1"},
    }


def test_s3_create_bucket_omits_location_constraint_for_us_east_1() -> None:
    adapter = S3StorageAdapter(_config(StorageProtocol.S3, region="us-east-1"))
    calls: dict[str, Any] = {}

    class _Client:
        def create_bucket(self, **kwargs: Any) -> None:
            calls.update(kwargs)

    adapter.client = _Client()  # type: ignore[assignment]
    adapter._sync_create_bucket("new-bucket")
    assert calls == {"Bucket": "new-bucket"}


def test_obs_create_bucket_raises_on_error_status() -> None:
    adapter = ObsStorageAdapter(_config(StorageProtocol.OBS, endpoint="obs.example.com", region="cn-north-4"))

    class _Resp:
        status = 403
        errorMessage = "AccessDenied"

    class _Client:
        def createBucket(self, bucketName: str) -> _Resp:  # noqa: N802 - 对齐 OBS SDK 命名
            return _Resp()

    adapter.client = _Client()  # type: ignore[assignment]
    with pytest.raises(CustomException, match="OBS 创建桶失败"):
        adapter._sync_create_bucket("new-bucket")


def test_obs_create_bucket_succeeds_on_2xx() -> None:
    adapter = ObsStorageAdapter(_config(StorageProtocol.OBS, endpoint="obs.example.com", region="cn-north-4"))

    class _Resp:
        status = 200
        errorMessage = ""

    class _Client:
        def createBucket(self, bucketName: str) -> _Resp:  # noqa: N802 - 对齐 OBS SDK 命名
            return _Resp()

    adapter.client = _Client()  # type: ignore[assignment]
    adapter._sync_create_bucket("new-bucket")  # 不抛异常即通过
