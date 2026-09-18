"""绘图工作台测试共享 fixture

- ``iw_db``：独立 sqlite 文件库（函数级、绑定当前事件循环、开启 FK 级联），
  供 model / service / engine 层的异步直测使用，避开全局引擎跨事件循环复用问题。
- 仅建绘图工作台 4 表 + ``sys_user``（UserMixin 的 FK 目标），加速建库。
"""

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.base_model import MappedBase
from app.core.base_schema import AuthSchema, CoreUserSchema
from app.modules.image_workbench.config.model import (
    ImageWorkbenchModelModel,
    ImageWorkbenchServiceModel,
)
from app.modules.image_workbench.job.model import (
    ImageWorkbenchArtifactModel,
    ImageWorkbenchJobArtifactRefModel,
    ImageWorkbenchJobModel,
)
from app.modules.system.dept.model import DeptModel
from app.modules.system.user.model import UserModel
from app.modules.task.storage.node.model import StorageNodeModel
from app.utils.import_util import ImportUtil

# 注册全部模型（含 sys_user 等 FK 依赖表）
ImportUtil.find_models(MappedBase)

# sys_user 对 sys_dept 有 FK（use_alter），sqlite 下开启 FK 校验时该表必须一并建出
# task_storage_node 是产物转存的落点（引擎经 storage 模块取默认节点）
_IW_TABLES = [
    DeptModel.__table__,
    UserModel.__table__,
    StorageNodeModel.__table__,
    ImageWorkbenchServiceModel.__table__,
    ImageWorkbenchModelModel.__table__,
    ImageWorkbenchJobModel.__table__,
    ImageWorkbenchArtifactModel.__table__,
    ImageWorkbenchJobArtifactRefModel.__table__,
]


@pytest.fixture(scope="session", autouse=True)
def _fast_sqlite_writes():
    """关闭操作日志与登录审计的落库写入（sqlite 测试环境的锁竞争源）。"""
    from unittest.mock import patch

    from app.config.setting import settings

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


@pytest.fixture
async def iw_db(tmp_path):
    """返回绑定当前事件循环的会话工厂（全部表已建好，FK 级联开启）。"""
    db_path = tmp_path / "image_workbench_test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_pragma(dbapi_conn, _record) -> None:
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: MappedBase.metadata.create_all(sync_conn, tables=_IW_TABLES))
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
def fake_auth() -> AuthSchema:
    """服务层直测用的认证上下文（id=0 → 不填审计人，避开 sys_user FK）。"""
    return AuthSchema(user=CoreUserSchema(id=0, username="tester", name="测试员"))
