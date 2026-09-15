"""渠道评估测试共享 fixture

- ``eval_db``：独立 sqlite 文件库（函数级、绑定当前事件循环、开启 FK 级联），
  供 engine/service 层的异步直测使用，避开全局引擎跨事件循环复用问题。
- sqlite 测试环境下，请求事务与后台操作日志/登录审计写入存在锁竞争（各 30s busy timeout），
  与本模块行为无关，这里统一关闭以保持测试快速（不影响被测逻辑）。
"""

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config.setting import settings
from app.core.base_model import MappedBase
from app.core.base_schema import AuthSchema, CoreUserSchema
from app.modules.channel_eval.platform.model import ChannelEvalPlatformModel
from app.modules.channel_eval.report.model import ChannelEvalReportModel
from app.modules.channel_eval.run.model import (
    ChannelEvalRunComboModel,
    ChannelEvalRunModel,
    ChannelEvalSampleModel,
)
from app.modules.system.user.model import UserModel
from app.utils.import_util import ImportUtil

# 注册全部模型（含 sys_user 等 FK 依赖表）
ImportUtil.find_models(MappedBase)

# eval_db 只建渠道评估 5 表 + sys_user（UserMixin FK 目标），加速建库
_EVAL_TABLES = [
    UserModel.__table__,
    ChannelEvalPlatformModel.__table__,
    ChannelEvalRunModel.__table__,
    ChannelEvalRunComboModel.__table__,
    ChannelEvalSampleModel.__table__,
    ChannelEvalReportModel.__table__,
]


@pytest.fixture(scope="session", autouse=True)
def _fast_sqlite_writes():
    """关闭操作日志与登录审计的落库写入（sqlite 测试环境的锁竞争源）。"""
    from unittest.mock import patch

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
async def eval_db(tmp_path):
    """返回绑定当前事件循环的会话工厂（全部表已建好，FK 级联开启）。"""
    db_path = tmp_path / "channel_eval_test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_pragma(dbapi_conn, _record) -> None:
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: MappedBase.metadata.create_all(sync_conn, tables=_EVAL_TABLES))
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
def fake_auth() -> AuthSchema:
    """服务层直测用的认证上下文（id=0 → 不填审计人，避开 sys_user FK）。"""
    return AuthSchema(user=CoreUserSchema(id=0, username="tester", name="测试员"))
