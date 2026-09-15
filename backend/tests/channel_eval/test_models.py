"""渠道评估 model 层测试（任务 1.3）

覆盖：建表、默认值、外键级联删除语义（run/report 及关联表）。
"""

from sqlalchemy import delete, inspect, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.modules.channel_eval.platform.model import ChannelEvalPlatformModel
from app.modules.channel_eval.report.model import ChannelEvalReportModel
from app.modules.channel_eval.run.model import (
    ChannelEvalRunComboModel,
    ChannelEvalRunModel,
    ChannelEvalSampleModel,
)

EXPECTED_TABLES = {
    "channel_eval_platform",
    "channel_eval_run",
    "channel_eval_run_combo",
    "channel_eval_sample",
    "channel_eval_report",
}


async def test_tables_created(eval_db: async_sessionmaker) -> None:
    """5 张表全部建表。"""
    async with eval_db() as db:
        tables = set(await db.run_sync(lambda sess: inspect(sess.bind).get_table_names()))  # type: ignore[arg-type]
    assert EXPECTED_TABLES <= tables


async def test_model_defaults(eval_db: async_sessionmaker) -> None:
    """默认值：run=pending、combo 计数 0、sample be_matched=False、platform 非基准。"""
    async with eval_db() as db, db.begin():
        platform = ChannelEvalPlatformModel(name="p1", type="admin", base_url="https://p1.example.com", test_key_enc="enc")
        db.add(platform)
        await db.flush()
        run = ChannelEvalRunModel(platform_id=platform.id, params_json={"request_count": 3})
        db.add(run)
        await db.flush()
        combo = ChannelEvalRunComboModel(run_id=run.id, channel_id=70, channel_name="ch70", model="m1")
        db.add(combo)
        await db.flush()
        sample = ChannelEvalSampleModel(combo_id=combo.id, seq=1)
        db.add(sample)
        report = ChannelEvalReportModel(name="r1", run_ids=[run.id])
        db.add(report)

    async with eval_db() as db:
        run = (await db.execute(select(ChannelEvalRunModel))).scalars().one()
        assert run.status == "pending"
        assert run.params_json == {"request_count": 3}
        assert run.started_at is None and run.finished_at is None
        # 评估令牌字段（指定渠道专用，创建时写入，默认空）
        assert run.eval_token_id is None
        assert run.eval_token_key_enc is None
        combo = (await db.execute(select(ChannelEvalRunComboModel))).scalars().one()
        assert (combo.status, combo.planned, combo.success, combo.failed, combo.anomaly) == ("pending", 0, 0, 0, 0)
        sample = (await db.execute(select(ChannelEvalSampleModel))).scalars().one()
        assert sample.be_matched is False
        assert sample.is_warmup is False
        platform = (await db.execute(select(ChannelEvalPlatformModel))).scalars().one()
        assert platform.is_baseline is False and platform.status == 0
        report = (await db.execute(select(ChannelEvalReportModel))).scalars().one()
        assert report.run_ids == [run.id]
        assert report.baseline_run_id is None and report.status == 0


async def test_run_cascade_deletes_combos_and_samples(eval_db: async_sessionmaker) -> None:
    """硬删除 run → 关联 combo 与 sample 经 FK 级联删除。"""
    async with eval_db() as db, db.begin():
        platform = ChannelEvalPlatformModel(name="p1", type="admin", base_url="https://p1.example.com", test_key_enc="enc")
        db.add(platform)
        await db.flush()
        run = ChannelEvalRunModel(platform_id=platform.id)
        db.add(run)
        await db.flush()
        for i in range(2):
            combo = ChannelEvalRunComboModel(run_id=run.id, channel_id=70 + i, channel_name=f"ch{70 + i}", model="m1")
            db.add(combo)
            await db.flush()
            db.add(ChannelEvalSampleModel(combo_id=combo.id, seq=1))
        run_id = run.id

    async with eval_db() as db, db.begin():
        await db.execute(delete(ChannelEvalRunModel).where(ChannelEvalRunModel.id == run_id))

    async with eval_db() as db:
        assert (await db.execute(select(ChannelEvalRunComboModel))).scalars().all() == []
        assert (await db.execute(select(ChannelEvalSampleModel))).scalars().all() == []


async def test_platform_cascade_deletes_children(eval_db: async_sessionmaker) -> None:
    """硬删除 platform → run 级联删除（run 再级联 combo/sample）。"""
    async with eval_db() as db, db.begin():
        platform = ChannelEvalPlatformModel(name="p1", type="admin", base_url="https://p1.example.com", test_key_enc="enc")
        db.add(platform)
        await db.flush()
        run = ChannelEvalRunModel(platform_id=platform.id)
        db.add(run)
        await db.flush()
        combo = ChannelEvalRunComboModel(run_id=run.id, channel_id=70, channel_name="ch70", model="m1")
        db.add(combo)
        await db.flush()
        db.add(ChannelEvalSampleModel(combo_id=combo.id, seq=1))
        platform_id = platform.id

    async with eval_db() as db, db.begin():
        await db.execute(delete(ChannelEvalPlatformModel).where(ChannelEvalPlatformModel.id == platform_id))

    async with eval_db() as db:
        assert (await db.execute(select(ChannelEvalRunModel))).scalars().all() == []
        assert (await db.execute(select(ChannelEvalRunComboModel))).scalars().all() == []
        assert (await db.execute(select(ChannelEvalSampleModel))).scalars().all() == []
