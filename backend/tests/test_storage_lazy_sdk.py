"""存储适配器 SDK 懒加载回归测试（参照 FastHub 的 lazy_sdk 实践）。

不变量：
1. 导入适配器工厂（会引入全部 7 个适配器）**不得**拉起任何重型 SDK；
2. 只有构造对应协议的客户端时才导入该 SDK。

用子进程验证 `sys.modules`：本进程 import 过的东西无法"卸载"，只能隔离观察。
"""

import json
import subprocess
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_HEAVY = ("boto3", "botocore", "qcloud_cos", "obs", "paramiko", "alibabacloud_oss_v2")

_PROBE = r'''
import json, sys
import app.modules.task.storage.core.factory  # 触发全部适配器导入
HEAVY = ("boto3", "botocore", "qcloud_cos", "obs", "paramiko", "alibabacloud_oss_v2")
print("__BEFORE__" + json.dumps([m for m in HEAVY if m in sys.modules]))

from app.modules.task.storage.core.base import StorageAdapterConfig, StorageProtocol
from app.modules.task.storage.core.s3_adapter import S3StorageAdapter
try:
    S3StorageAdapter(
        StorageAdapterConfig(
            protocol=StorageProtocol.S3,
            port=443,
            endpoint="https://s3.example.com",
            bucket="demo",
            username="access-key",
            password="secret-key",
        )
    )
except Exception as exc:  # 构造期不需要联网，允许因参数不足抛错
    print("__S3ERR__" + type(exc).__name__ + ": " + str(exc)[:200])

print("__AFTER__" + json.dumps([m for m in HEAVY if m in sys.modules]))
'''


def _probe() -> dict[str, list[str]]:
    """在独立进程中导入工厂并构造 S3 适配器，返回前后已载入的重型 SDK。"""
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=_BACKEND_DIR,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, f"子进程失败: {proc.stderr[-2000:]}"
    data: dict[str, list[str]] = {}
    for line in proc.stdout.splitlines():
        if line.startswith("__BEFORE__"):
            data["before"] = json.loads(line[len("__BEFORE__") :])
        elif line.startswith("__AFTER__"):
            data["after"] = json.loads(line[len("__AFTER__") :])
    assert "before" in data and "after" in data, f"探针输出异常: {proc.stdout[-1000:]}"
    return data


def test_importing_factory_does_not_load_heavy_sdks() -> None:
    """导入工厂不能拉起任何重型 SDK（冷启动不为用不到的协议付代价）。"""
    data = _probe()
    assert data["before"] == [], f"导入工厂即拉起重型 SDK: {data['before']}"


def test_constructing_s3_adapter_loads_only_its_own_sdk() -> None:
    """构造 S3 客户端只应载入 boto3/botocore，不得连带载入其它协议 SDK。"""
    data = _probe()
    after = data["after"]
    assert "boto3" in after, f"S3 构造后未导入 boto3: {after}"
    for module in ("qcloud_cos", "obs", "alibabacloud_oss_v2", "paramiko"):
        assert module not in after, f"S3 构造连带导入了 {module}: {after}"
