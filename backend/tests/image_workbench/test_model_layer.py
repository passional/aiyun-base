"""绘图工作台 model 层测试（任务 3.4）

覆盖：建表、默认值、外键关系、``created_id`` 写入、软删标记（设计 D2），
以及产物表的默认值、外键关系与「取底图即 ``z_index IS NULL``」（设计 D7）。
"""

import pytest
from sqlalchemy import delete, inspect, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import joinedload, selectinload

from app.core.base_crud import CRUDBase
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
from app.modules.image_workbench.job.service import ImageWorkbenchJobService
from app.modules.system.user.model import UserModel

EXPECTED_TABLES = {
    "image_workbench_service",
    "image_workbench_model",
    "image_workbench_job",
    "image_workbench_artifact",
    "image_workbench_job_artifact_ref",
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


async def _seed_job(db, *, model_id: str = "m1") -> ImageWorkbenchJobModel:
    """落一套 服务 + 模型 + 任务，供产物相关用例挂产物行。"""
    service = ImageWorkbenchServiceModel(name=f"ark-plan-{model_id}", base_url="https://ark.example.com/api/plan/v3")
    db.add(service)
    await db.flush()
    model = ImageWorkbenchModelModel(service_id=service.id, model_id=model_id, display_name=model_id.upper())
    db.add(model)
    await db.flush()
    job = ImageWorkbenchJobModel(service_id=service.id, model_pk=model.id, model_id=model_id, prompt="一只在雪地里的猫", prompt_final="一只在雪地里的猫")
    db.add(job)
    await db.flush()
    return job


async def test_job_defaults(iw_db: async_sessionmaker) -> None:
    """绘图任务默认 pending，用量 / 耗时 / 参考图 / 起止时间均为空。"""
    async with iw_db() as db, db.begin():
        await _seed_job(db)

    async with iw_db() as db:
        job = (await db.execute(select(ImageWorkbenchJobModel))).scalars().one()
        assert job.status == "pending"
        assert job.error is None
        assert job.usage_json is None
        assert job.elapsed_ms is None
        assert job.params_json is None
        assert job.reference_images_json is None
        assert job.started_at is None and job.finished_at is None
        assert job.is_deleted is False


async def test_artifact_defaults_and_base_identification(iw_db: async_sessionmaker) -> None:
    """产物默认值：位置框 / 名称 / 描述 / 尺寸 / 格式可空；无层级即底图。"""
    async with iw_db() as db, db.begin():
        job = await _seed_job(db)
        db.add(ImageWorkbenchArtifactModel(job_id=job.id, file_path="image_workbench/2026/09/uuid/base.png"))
        db.add(
            ImageWorkbenchArtifactModel(
                job_id=job.id,
                z_index=2,
                name="人物",
                description="画面前景的人物",
                bounding_box_json={"absolute": [600, 900, 1400, 2000], "normalized": [292, 439, 683, 976]},
                file_path="image_workbench/2026/09/uuid/layer_02.png",
                image_size="2048x2048",
                output_format="png",
            )
        )

    async with iw_db() as db:
        rows = (await db.execute(select(ImageWorkbenchArtifactModel).order_by(ImageWorkbenchArtifactModel.z_index))).scalars().all()
        base = next(row for row in rows if row.z_index is None)
        layer = next(row for row in rows if row.z_index == 2)
        assert base.is_base is True and layer.is_base is False
        assert base.name is None and base.description is None and base.bounding_box_json is None
        assert base.image_size is None and base.output_format is None
        assert base.is_deleted is False
        assert layer.bounding_box_json["normalized"] == [292, 439, 683, 976]


async def test_base_artifact_is_the_one_without_z_index(iw_db: async_sessionmaker) -> None:
    """「取底图」就是 ``z_index IS NULL``——而不是「层级为 0」。"""
    async with iw_db() as db, db.begin():
        job = await _seed_job(db)
        db.add(ImageWorkbenchArtifactModel(job_id=job.id, file_path="b.png"))
        # 层级 0 是一个**图层**，不是底图：底图用「无层级」表达
        db.add(ImageWorkbenchArtifactModel(job_id=job.id, z_index=0, file_path="layer_00.png"))

    async with iw_db() as db:
        base_rows = (await db.execute(select(ImageWorkbenchArtifactModel).where(ImageWorkbenchArtifactModel.z_index.is_(None)))).scalars().all()
        assert [row.file_path for row in base_rows] == ["b.png"]


async def test_artifact_delete_cascades_with_job(iw_db: async_sessionmaker) -> None:
    """产物随任务被物理删除（ondelete=CASCADE 的数据库侧兜底）。"""
    async with iw_db() as db, db.begin():
        job = await _seed_job(db)
        db.add(ImageWorkbenchArtifactModel(job_id=job.id, file_path="b.png"))
        job_id = job.id

    async with iw_db() as db, db.begin():
        await db.execute(delete(ImageWorkbenchJobModel).where(ImageWorkbenchJobModel.id == job_id))

    async with iw_db() as db:
        assert (await db.execute(select(ImageWorkbenchArtifactModel))).scalars().all() == []


async def test_job_reaches_artifacts_and_back(iw_db: async_sessionmaker) -> None:
    """两级关系：任务经 ``artifacts`` 可达产物，产物经 ``job`` 反向可达。"""
    async with iw_db() as db, db.begin():
        job = await _seed_job(db)
        db.add(ImageWorkbenchArtifactModel(job_id=job.id, file_path="b.png"))

    async with iw_db() as db:
        job = (await db.execute(select(ImageWorkbenchJobModel).options(selectinload(ImageWorkbenchJobModel.artifacts)))).scalars().one()
        assert [row.file_path for row in job.artifacts] == ["b.png"]
        artifact = (await db.execute(select(ImageWorkbenchArtifactModel).options(joinedload(ImageWorkbenchArtifactModel.job)))).scalars().one()
        assert artifact.job.id == job.id


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
        db.add(ImageWorkbenchJobModel(service_id=service.id, model_pk=model.id, model_id="m1", prompt="p", prompt_final="p"))
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
        job = await CRUDBase(ImageWorkbenchJobModel, auth, db).create({"service_id": service.id, "model_pk": model.id, "model_id": "m1", "prompt": "p", "prompt_final": "p"})
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


# ─────────────── 2.1 产物引用表 ───────────────


async def _seed_ref_chain(db, *, ref_count: int = 2) -> tuple[ImageWorkbenchJobModel, list[ImageWorkbenchArtifactModel]]:
    """一套 服务 + 模型 + 一次图层拆分任务（底图 + 若干图层），供引用关系用例使用。"""
    service = ImageWorkbenchServiceModel(name="ref-chain", base_url="https://ark.example.com/api/plan/v3")
    db.add(service)
    await db.flush()
    model = ImageWorkbenchModelModel(service_id=service.id, model_id="m1", display_name="M1")
    db.add(model)
    await db.flush()
    job = ImageWorkbenchJobModel(service_id=service.id, model_pk=model.id, model_id="m1", prompt="p", prompt_final="p")
    db.add(job)
    await db.flush()
    artifacts = [
        ImageWorkbenchArtifactModel(job_id=job.id, z_index=None if index == 0 else index, file_path=f"image_workbench/{job.uuid}/a{index}.png")
        for index in range(ref_count)
    ]
    db.add_all(artifacts)
    await db.flush()
    return job, artifacts


async def test_artifact_ref_unique_per_job_and_artifact(iw_db: async_sessionmaker) -> None:
    """``UNIQUE(job_id, artifact_id)``：同一任务对同一产物只允许落一行。"""
    async with iw_db() as db, db.begin():
        job, artifacts = await _seed_ref_chain(db)
        db.add(ImageWorkbenchJobArtifactRefModel(job_id=job.id, artifact_id=artifacts[0].id))
        job_id, artifact_id = job.id, artifacts[0].id

    with pytest.raises(IntegrityError):
        async with iw_db() as db, db.begin():
            db.add(ImageWorkbenchJobArtifactRefModel(job_id=job_id, artifact_id=artifact_id))


async def _ref_columns(iw_db: async_sessionmaker) -> list[dict]:
    async with iw_db() as db:
        return await db.run_sync(lambda sess: inspect(sess.bind).get_columns("image_workbench_job_artifact_ref"))  # type: ignore[arg-type]


async def test_artifact_ref_reverse_lookup_is_indexed(iw_db: async_sessionmaker) -> None:
    """反查（按产物找引用它的任务）走索引：``artifact_id`` 上有独立索引，删除保护才不会全表扫。"""
    async with iw_db() as db:
        indexes = await db.run_sync(lambda sess: {index["name"]: index["column_names"] for index in inspect(sess.bind).get_indexes("image_workbench_job_artifact_ref")})  # type: ignore[arg-type]
    assert any(columns == ["artifact_id"] for columns in indexes.values()), f"artifact_id 上缺索引: {indexes}"
    # 两类引用合写一张表，且不给引用标类型（design D6）
    assert "ref_type" not in {column["name"] for column in await _ref_columns(iw_db)}


async def test_artifact_ref_cascades_with_job_and_artifact(iw_db: async_sessionmaker) -> None:
    """任务行与产物行被物理删除时，引用行随之消失——不留下把已删产物标成「被引用」的残行。"""
    async with iw_db() as db, db.begin():
        job, artifacts = await _seed_ref_chain(db)
        db.add_all([ImageWorkbenchJobArtifactRefModel(job_id=job.id, artifact_id=artifact.id) for artifact in artifacts])
        artifact_id = artifacts[0].id

    async with iw_db() as db, db.begin():
        assert (await db.execute(select(ImageWorkbenchJobArtifactRefModel))).scalars().all()
        await db.execute(delete(ImageWorkbenchArtifactModel).where(ImageWorkbenchArtifactModel.id == artifact_id))

    async with iw_db() as db:
        remaining = (await db.execute(select(ImageWorkbenchJobArtifactRefModel.artifact_id))).scalars().all()
        assert remaining == [artifacts[1].id], "被删产物上的引用行必须一并消失"


# ─────────────── 2.2 按产物反查引用它的任务 ───────────────


async def test_referencing_jobs_counts_only_other_jobs(iw_db: async_sessionmaker, fake_auth: AuthSchema) -> None:
    """反查的三种输入：有其它任务引用 / 无引用 / 只有自身引用。

    「只有自身引用」必须算作**没有**引用者——否则任务永远删不掉自己。
    """
    async with iw_db() as db, db.begin():
        source, artifacts = await _seed_ref_chain(db, ref_count=1)
        artifact_id = artifacts[0].id
        # 自身引用：真实链路不会产生它（提交时任务还没有标识），但反查必须能正确处理
        db.add(ImageWorkbenchJobArtifactRefModel(job_id=source.id, artifact_id=artifact_id))
        other = ImageWorkbenchJobModel(service_id=source.service_id, model_pk=source.model_pk, model_id="m1", prompt="p", prompt_final="p")
        db.add(other)
        await db.flush()
        source_id, other_id = source.id, other.id

    service = ImageWorkbenchJobService(fake_auth, db)
    async with iw_db() as db:
        service = ImageWorkbenchJobService(fake_auth, db)
        # 只有自身引用 → 不算被引用
        assert await service.referencing_jobs([artifact_id], exclude_job_ids={source_id}) == []
        # 其它任务引用 → 查到它
        async with iw_db() as writer, writer.begin():
            writer.add(ImageWorkbenchJobArtifactRefModel(job_id=other_id, artifact_id=artifact_id))
        found = await service.referencing_jobs([artifact_id], exclude_job_ids={source_id})
        assert [job.id for job in found] == [other_id]
        # 无引用 → 空
        assert await service.referencing_jobs([999999]) == []


async def test_referencing_jobs_ignores_soft_deleted_referrers(iw_db: async_sessionmaker, fake_auth: AuthSchema) -> None:
    """已软删的任务不算引用者：它已不在任何人的可见范围里，继续拿它挡删除只会让用户无从下手。"""
    async with iw_db() as db, db.begin():
        source, artifacts = await _seed_ref_chain(db, ref_count=1)
        artifact_id = artifacts[0].id
        referrer = ImageWorkbenchJobModel(service_id=source.service_id, model_pk=source.model_pk, model_id="m1", prompt="p", prompt_final="p", is_deleted=True)
        db.add(referrer)
        await db.flush()
        db.add(ImageWorkbenchJobArtifactRefModel(job_id=referrer.id, artifact_id=artifact_id))
        referrer_id = referrer.id

    async with iw_db() as db:
        service = ImageWorkbenchJobService(fake_auth, db)
        assert await service.referencing_jobs([artifact_id]) == []
        # 未软删时同样查得到（确认上一条不是因为别的原因为空）
        async with iw_db() as writer, writer.begin():
            await writer.execute(update(ImageWorkbenchJobModel).where(ImageWorkbenchJobModel.id == referrer_id).values(is_deleted=False))
        assert [job.id for job in await service.referencing_jobs([artifact_id])] == [referrer_id]


async def test_referent_artifact_ids_returns_every_source(iw_db: async_sessionmaker, fake_auth: AuthSchema) -> None:
    """重试要原样继承的来源引用：``referent_artifact_ids`` 返回该任务引用过的全部产物。"""
    async with iw_db() as db, db.begin():
        source, artifacts = await _seed_ref_chain(db, ref_count=3)
        referrer = ImageWorkbenchJobModel(service_id=source.service_id, model_pk=source.model_pk, model_id="m1", prompt="p", prompt_final="p")
        db.add(referrer)
        await db.flush()
        db.add_all([ImageWorkbenchJobArtifactRefModel(job_id=referrer.id, artifact_id=artifact.id) for artifact in artifacts])
        referrer_id = referrer.id
        expected = sorted(artifact.id for artifact in artifacts)

    async with iw_db() as db:
        assert await ImageWorkbenchJobService(fake_auth, db).referent_artifact_ids(referrer_id) == expected
