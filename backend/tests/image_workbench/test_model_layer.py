"""绘图工作台 model 层测试（任务 1.3）

覆盖：建表、默认值、外键关系、``created_id`` 写入、软删标记（设计 D2）。
"""

import pytest
from sqlalchemy import delete, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import joinedload, selectinload

from app.core.base_crud import CRUDBase
from app.core.base_schema import AuthSchema, CoreUserSchema
from app.modules.image_workbench.config.model import (
    ImageWorkbenchModelModel,
    ImageWorkbenchServiceModel,
)
from app.modules.image_workbench.job.model import ImageWorkbenchJobModel
from app.modules.system.user.model import UserModel

EXPECTED_TABLES = {
    "image_workbench_service",
    "image_workbench_model",
    "image_workbench_job",
}


async def _seed_user(db, username: str = "tester") -> UserModel:
    user = UserModel(username=username, password="x", name="测试员")
    db.add(user)
    await db.flush()
    return user


async def test_tables_created(iw_db: async_sessionmaker) -> None:
    """3 张表全部建表。"""
    async with iw_db() as db:
        tables = set(await db.run_sync(lambda sess: inspect(sess.bind).get_table_names()))  # type: ignore[arg-type]
    assert EXPECTED_TABLES <= tables


async def test_service_and_model_defaults(iw_db: async_sessionmaker) -> None:
    """绘图服务默认启用 / 接入方式 ark；绘图模型默认启用且能力声明为空。"""
    async with iw_db() as db, db.begin():
        service = ImageWorkbenchServiceModel(name="ark-plan", base_url="https://ark.example.com/api/plan/v3")
        db.add(service)
        await db.flush()
        model = ImageWorkbenchModelModel(service_id=service.id, model_id="doubao-seedream-5-0-pro", display_name="Seedream 5.0 pro")
        db.add(model)

    async with iw_db() as db:
        service = (await db.execute(select(ImageWorkbenchServiceModel))).scalars().one()
        assert service.dialect == "ark"
        assert service.status == 0
        assert service.api_key_enc is None
        assert service.is_deleted is False
        model = (await db.execute(select(ImageWorkbenchModelModel))).scalars().one()
        assert model.status == 0
        assert model.capability_json is None
        assert model.is_deleted is False


async def test_job_defaults(iw_db: async_sessionmaker) -> None:
    """绘图任务默认 pending，用量 / 耗时 / 产物 / 起止时间均为空。"""
    async with iw_db() as db, db.begin():
        service = ImageWorkbenchServiceModel(name="ark-plan", base_url="https://ark.example.com/api/plan/v3")
        db.add(service)
        await db.flush()
        model = ImageWorkbenchModelModel(service_id=service.id, model_id="m1", display_name="M1")
        db.add(model)
        await db.flush()
        db.add(
            ImageWorkbenchJobModel(
                service_id=service.id,
                model_pk=model.id,
                model_id="m1",
                prompt="一只在雪地里的猫",
            )
        )

    async with iw_db() as db:
        job = (await db.execute(select(ImageWorkbenchJobModel))).scalars().one()
        assert job.status == "pending"
        assert job.error is None
        assert job.usage_json is None
        assert job.elapsed_ms is None
        assert job.image_path is None
        assert job.image_size is None
        assert job.started_at is None and job.finished_at is None
        assert job.is_deleted is False


async def test_model_belongs_to_service(iw_db: async_sessionmaker) -> None:
    """两级结构：模型经 ``service`` 关系可达所属服务，服务经 ``models`` 反向可达。"""
    async with iw_db() as db, db.begin():
        service = ImageWorkbenchServiceModel(name="ark-plan", base_url="https://ark.example.com/api/plan/v3")
        db.add(service)
        await db.flush()
        db.add(ImageWorkbenchModelModel(service_id=service.id, model_id="m1", display_name="M1"))
        db.add(ImageWorkbenchModelModel(service_id=service.id, model_id="m2", display_name="M2"))

    async with iw_db() as db:
        model = (
            (await db.execute(select(ImageWorkbenchModelModel).options(joinedload(ImageWorkbenchModelModel.service)).where(ImageWorkbenchModelModel.model_id == "m1")))
            .scalars()
            .one()
        )
        assert model.service.name == "ark-plan"
        service = (await db.execute(select(ImageWorkbenchServiceModel).options(selectinload(ImageWorkbenchServiceModel.models)))).scalars().one()
        assert sorted(m.model_id for m in service.models) == ["m1", "m2"]


async def test_service_cascade_deletes_models(iw_db: async_sessionmaker) -> None:
    """硬删除绘图服务 → 其下绘图模型经 FK 级联删除（ondelete=CASCADE）。"""
    async with iw_db() as db, db.begin():
        service = ImageWorkbenchServiceModel(name="ark-plan", base_url="https://ark.example.com/api/plan/v3")
        db.add(service)
        await db.flush()
        db.add(ImageWorkbenchModelModel(service_id=service.id, model_id="m1", display_name="M1"))
        service_id = service.id

    async with iw_db() as db, db.begin():
        await db.execute(delete(ImageWorkbenchServiceModel).where(ImageWorkbenchServiceModel.id == service_id))

    async with iw_db() as db:
        assert (await db.execute(select(ImageWorkbenchModelModel))).scalars().all() == []


async def test_job_fk_restricts_delete_of_service_and_model(iw_db: async_sessionmaker) -> None:
    """被绘图任务引用时拒绝物理删除（设计 D8 的数据库侧兜底，ondelete=RESTRICT）。"""
    async with iw_db() as db, db.begin():
        service = ImageWorkbenchServiceModel(name="ark-plan", base_url="https://ark.example.com/api/plan/v3")
        db.add(service)
        await db.flush()
        model = ImageWorkbenchModelModel(service_id=service.id, model_id="m1", display_name="M1")
        db.add(model)
        await db.flush()
        db.add(ImageWorkbenchJobModel(service_id=service.id, model_pk=model.id, model_id="m1", prompt="p"))
        model_id, service_id = model.id, service.id

    with pytest.raises(IntegrityError):
        async with iw_db() as db, db.begin():
            await db.execute(delete(ImageWorkbenchModelModel).where(ImageWorkbenchModelModel.id == model_id))

    with pytest.raises(IntegrityError):
        async with iw_db() as db, db.begin():
            await db.execute(delete(ImageWorkbenchServiceModel).where(ImageWorkbenchServiceModel.id == service_id))


async def test_job_created_id_written_and_soft_delete(iw_db: async_sessionmaker) -> None:
    """CRUD 层写入 ``created_id``；删除为软删（is_deleted / deleted_time / deleted_id 落库）。"""
    async with iw_db() as db, db.begin():
        user = await _seed_user(db)
        auth = AuthSchema(user=CoreUserSchema(id=user.id, username=user.username, name=user.name))
        service = await CRUDBase(ImageWorkbenchServiceModel, auth, db).create({"name": "ark-plan", "base_url": "https://ark.example.com/api/plan/v3"})
        model = await CRUDBase(ImageWorkbenchModelModel, auth, db).create({"service_id": service.id, "model_id": "m1", "display_name": "M1"})
        job = await CRUDBase(ImageWorkbenchJobModel, auth, db).create({"service_id": service.id, "model_pk": model.id, "model_id": "m1", "prompt": "p"})
        # 审计字段只有 job 表有（设计 D2：job 另继承 UserMixin）
        assert job.created_id == user.id
        assert not hasattr(service, "created_id") and not hasattr(model, "created_id")
        job_id = job.id

    async with iw_db() as db, db.begin():
        user = (await db.execute(select(UserModel))).scalars().one()
        auth = AuthSchema(user=CoreUserSchema(id=user.id, username=user.username))
        await CRUDBase(ImageWorkbenchJobModel, auth, db).delete(ids=[job_id])

    async with iw_db() as db:
        job = (await db.execute(select(ImageWorkbenchJobModel).where(ImageWorkbenchJobModel.id == job_id))).scalars().one()
        assert job.is_deleted is True
        assert job.deleted_time is not None
        assert job.deleted_id is not None
        # 软删后默认查询（include_deleted=False）不再返回
        auth = AuthSchema(user=CoreUserSchema(id=0))
        assert await CRUDBase(ImageWorkbenchJobModel, auth, db).count() == 0
