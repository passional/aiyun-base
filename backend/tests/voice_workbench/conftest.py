"""语音工作台测试共享夹具。

SQLite 下后台操作日志写入会与请求事务抢写锁（同 ``tests/image_workbench/conftest.py``
的处理）：默认关闭操作日志落库，需要断言的用例自行临时打开并在路由层拦截采集。
"""

from unittest.mock import patch

import pytest

from app.config.setting import settings


@pytest.fixture(scope="session", autouse=True)
def _fast_sqlite_writes():
    old_methods = settings.OPERATION_RECORD_METHOD
    settings.OPERATION_RECORD_METHOD = []

    async def _no_login_log(*args, **kwargs) -> None:
        return None

    login_log_patch = patch("app.modules.system.auth.service._write_login_log", new=_no_login_log)
    login_log_patch.start()
    try:
        yield
    finally:
        login_log_patch.stop()
        settings.OPERATION_RECORD_METHOD = old_methods
