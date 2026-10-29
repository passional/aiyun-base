"""绘图工作台 - 绘图任务业务层

本文件承载生成侧的全部业务（提交校验 / 并发闸门 / 历史与产物）。配置侧
（绘图服务与绘图模型）在 ``config/`` 目录。

**参数的处理只有一条路径**（设计 D5）：``resolve_params`` 按所选模型的清单校验
用户显式提交的参数并补齐默认值。首次提交与「以原参数重试」走的是**同一套**规则
（任务 4.5），重试不做任何绕过——清单改过之后原快照里不存在的参数会让重试被拒，
而不是被静默发给上游。

**可见范围**：列表、详情、取消、重试、删除、下载一律经 ``CRUDBase``（数据范围由
``Permission`` 按 ``created_id`` 施加），越权标识与「不存在」同答复，不泄露他人
任务的存在性（spec: access / 绘图任务的可见范围）。
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete, event, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_schema import AuthSchema, PageResultSchema
from app.core.exceptions import CustomException
from app.modules.task.storage.node.service import StorageNodeService
from app.utils.common_util import search_to_dict

from ..config.capability import (
    ImageWorkbenchCapabilitySchema,
    ImageWorkbenchModeMarkerSchema,
    parse_capability,
    render_prompt,
    resolve_params,
)
from ..config.model import ImageWorkbenchModelModel, ImageWorkbenchServiceModel
from .crud import ImageWorkbenchJobCRUD
from .engine import (
    artifact_url,
    delete_artifact,
    prepare_artifact_download,
    push_job_progress,
    request_cancel,
    resolve_reference_files,
    start_job_engine,
)
from .image_validation import detect_alpha_file, validate_reference_image_file
from .model import ImageWorkbenchArtifactModel, ImageWorkbenchJobArtifactRefModel, ImageWorkbenchJobModel
from .schema import (
    ACTIVE_STATUSES,
    STATUS_CANCELLED,
    STATUS_PENDING,
    TERMINAL_STATUSES,
    ImageWorkbenchArtifactOutSchema,
    ImageWorkbenchJobCreateSchema,
    ImageWorkbenchJobModelOptionSchema,
    ImageWorkbenchJobOutSchema,
    ImageWorkbenchJobQueryParam,
    ImageWorkbenchSelectionOutSchema,
    ImageWorkbenchSelectionSchema,
    status_label,
)


class ImageWorkbenchJobService:
    """绘图任务服务"""

    def __init__(self, auth: AuthSchema, db: AsyncSession) -> None:
        self.auth = auth
        self.db = db
        self.crud = ImageWorkbenchJobCRUD(auth, db)

    # ────────────────────── 工作台可选模型 ──────────────────────

    async def list_available_models(self) -> list[ImageWorkbenchJobModelOptionSchema]:
        """工作台可选模型：模型启用 且 所属服务启用 且 服务已配置凭证（设计 D8）。

        停用的绘图服务与其下模型天然不在此列表中；历史绘图任务不受影响
        （它们只依赖已落库的参数快照与产物行）。
        """
        stmt = (
            select(ImageWorkbenchModelModel, ImageWorkbenchServiceModel.name)
            .join(ImageWorkbenchServiceModel, ImageWorkbenchModelModel.service_id == ImageWorkbenchServiceModel.id)
            .where(
                ImageWorkbenchModelModel.status == 0,
                ImageWorkbenchModelModel.is_deleted == False,  # noqa: E712
                ImageWorkbenchServiceModel.status == 0,
                ImageWorkbenchServiceModel.is_deleted == False,  # noqa: E712
                ImageWorkbenchServiceModel.api_key_enc.isnot(None),
                ImageWorkbenchServiceModel.api_key_enc != "",
            )
            .order_by(ImageWorkbenchServiceModel.id, ImageWorkbenchModelModel.id)
        )
        rows = await self.db.execute(stmt)
        return [
            ImageWorkbenchJobModelOptionSchema(
                id=model.id,
                service_id=model.service_id,
                service_name=service_name,
                model_id=model.model_id,
                display_name=model.display_name,
                capability=parse_capability(model.capability_json),
            )
            for model, service_name in rows.all()
        ]

    # ─────────────────────────── 提交 ───────────────────────────

    async def submit(self, data: ImageWorkbenchJobCreateSchema) -> ImageWorkbenchJobOutSchema:
        """提交一次绘图任务：解析选区与来源 → 渲染最终提示词 → 落库 pending → 异步执行。

        选区、画面来源与最终提示词的解析在**提交这一侧**完成；``_create`` 只负责「已经解析
        干净的输入」——重试复用快照时走的正是同一条落库路径（design D11），它必须能绕过
        渲染，因此渲染不能长在 ``_create`` 里面。
        """
        model, service = await self._require_usable_model(data.model_pk)
        capability = parse_capability(model.capability_json)

        selections, ref_artifact_ids = await self._resolve_selections(data.selections, capability)
        prompt_final = self._render_final_prompt(capability, data.prompt, selections)
        # 画面来源是**这一次生成**，展开成它的全部产物（D3）——「记全」由构造保证
        ref_artifact_ids |= await self._composition_source_artifacts(data.composition_source_job_id)

        return await self._create(
            model=model,
            service=service,
            capability=capability,
            prompt=data.prompt,
            prompt_final=prompt_final,
            params=data.params,
            reference_images=data.reference_images,
            selections=selections,
            ref_artifact_ids=ref_artifact_ids,
        )

    async def _create(
        self,
        *,
        model: ImageWorkbenchModelModel,
        service: ImageWorkbenchServiceModel,
        capability: ImageWorkbenchCapabilitySchema,
        prompt: str,
        prompt_final: str,
        params: dict[str, Any],
        reference_images: list[str],
        selections: list[ImageWorkbenchSelectionOutSchema],
        ref_artifact_ids: set[int],
    ) -> ImageWorkbenchJobOutSchema:
        """落库一次绘图任务并启动异步执行（首次提交与重试共用）。

        参数 MUST 在提交时收敛到该模型的能力声明范围内（spec: generation / 提交绘图任务）：
        ``resolve_params`` 拒掉用户显式提交里的未知键、越界值与当前条件下不可用的参数，
        并为「当前可用且用户未提交」的参数补上清单默认值。**默认值必须在服务端补齐**，
        否则 ``watermark`` 这类「上游默认与产品默认相反」的字段漏传即得到相反结果。

        重试走的是**同一套**校验与补齐规则：把原任务的参数快照当作「用户显式提交的参数」
        重新提交，不做任何绕过。
        """
        resolved_references, facts = await self._resolve_reference_images(reference_images, capability)
        resolved_params, _ = resolve_params(capability, submitted=params, facts=facts)

        await self._check_concurrency()

        try:
            job = await self.crud.create(
                {
                    "service_id": service.id,
                    "model_pk": model.id,
                    # 标识快照：上游模型标识漂移不影响历史展示（design D2）
                    "model_id": model.model_id,
                    # 数据库唯一活跃槽位：终态释放为 NULL；并发插入时只能有一个成功
                    "active_owner_id": self.auth.user.id or None,
                    # 原话与最终提示词都存：前者回看、后者追溯（design D5）
                    "prompt": prompt,
                    "prompt_final": prompt_final,
                    # 选区是快照而非引用，与 params_json 同一取向（design D5/Migration Plan）
                    "selections_json": [item.model_dump(exclude_none=True) for item in selections],
                    # 快照 == 实际发给上游的参数集（含补齐的默认值）
                    "params_json": resolved_params,
                    # 参考图不是参数（D4），单独存：重试要按原样重新提交
                    "reference_images_json": resolved_references,
                    "status": STATUS_PENDING,
                }
            )
        except (CustomException, IntegrityError) as exc:
            if self._is_active_slot_conflict(exc):
                raise CustomException(
                    msg="您有正在进行的绘图任务，请等待其结束或取消后再提交",
                    code=400,
                ) from exc
            raise
        await self.db.flush()
        await self._save_artifact_refs(job.id, ref_artifact_ids)
        # 事务提交后再启动后台执行：提前启动读不到未提交行（design D5）
        self._launch_after_commit(job.id)
        return await self._to_out(job)

    # ─────────────────── 选区、最终提示词与来源产物 ───────────────────

    async def _resolve_selections(
        self,
        selections: list[ImageWorkbenchSelectionSchema],
        capability: ImageWorkbenchCapabilitySchema,
    ) -> tuple[list[ImageWorkbenchSelectionOutSchema], set[int]]:
        """解析并校验选区，返回 ``(选区快照, 选区来源产物ID集合)``。

        两种位置来源在这里合流成同一个坐标框：点选图层的坐标**由服务端读该产物的位置框**，
        自行划框的坐标由前端给出。合流之后，校验、渲染与落库都只有一条路径。

        「所选模型声明含交互编辑」是前置条件：绕过前端直接提交选区的请求同样在这里被拒
        （spec: generation / 交互编辑与选区「即使绕过前端提交选区，后端也拒绝该请求」）。
        """
        if not selections:
            return [], set()
        marker = self._require_marker(capability)

        seen: set[int] = set()
        resolved: list[ImageWorkbenchSelectionOutSchema] = []
        sources: set[int] = set()
        for item in selections:
            if item.index in seen:
                raise CustomException(msg=f"选区序号 #{item.index} 重复，每个选区必须有不同的序号", code=400)
            seen.add(item.index)

            bbox = item.bbox
            if item.artifact_id is not None:
                artifact = await self.require_visible_artifact(item.artifact_id)
                bbox = self._normalized_bbox(artifact, item.index)
                sources.add(artifact.id)
            self._check_bbox(bbox, marker, item.index)
            resolved.append(ImageWorkbenchSelectionOutSchema(index=item.index, bbox=bbox, artifact_id=item.artifact_id))
        return resolved, sources

    @staticmethod
    def _require_marker(capability: ImageWorkbenchCapabilitySchema) -> ImageWorkbenchModeMarkerSchema:
        """取该模型声明的坐标标记语法；没有即「不支持交互编辑」。"""
        mode = capability.coordinate_mode
        if mode is None or mode.marker is None:
            raise CustomException(msg="所选绘图模型不支持交互编辑，无法提交选区，请改为不带选区的提交", code=400)
        return mode.marker

    def _render_final_prompt(
        self,
        capability: ImageWorkbenchCapabilitySchema,
        prompt: str,
        selections: list[ImageWorkbenchSelectionSchema],
    ) -> str:
        """渲染最终提示词；两种「对不上」都拒绝提交并指出是哪一个（design D4 两条硬规则）。

        - 提示词引用了选区、却拿不到对应坐标 → 拒绝：坐标不能被静默丢弃；
        - 有选区、提示词里却没有任何引用 → 同样拒绝：这个坐标白给了，提交出去只会让人
          以为它生效了。
        """
        final, missing, unused = render_prompt(capability, prompt, [(item.index, item.bbox) for item in selections])
        if missing:
            raise CustomException(
                msg=f"提示词引用了选区 {'、'.join(f'#{index}' for index in missing)}，但没有对应的坐标，请为该选区指定位置或删掉这段引用",
                code=400,
            )
        if unused:
            raise CustomException(
                msg=f"选区 {'、'.join(f'#{index}' for index in unused)} 已指定位置，但提示词里没有引用它，请在提示词里写入对应的区域占位符或删除该选区",
                code=400,
            )
        return final

    async def _composition_source_artifacts(self, job_id: int | None) -> set[int]:
        """画面来源：把「产出这些产物的那次生成」展开成它的**全部**产物（design D3）。

        提交的是**任务标识**而不是前端列出的产物清单：「记全」因此是构造出来的，而不是
        前端声明的——前端列产物时服务端无从校验列得全不全，而列不全正是这条要防的事。
        """
        if job_id is None:
            return set()
        job = await self.require_job(job_id)
        artifacts = await self.artifacts_of([job.id])
        return {artifact.id for artifact in artifacts.get(job.id, [])}

    def _normalized_bbox(self, artifact: ImageWorkbenchArtifactModel, index: int) -> list[int]:
        """读产物的**归一化**位置框（0–999，与坐标标记同一个坐标系）。

        没有归一化表示时拒绝，而不是拿绝对的像素框去凑：两者坐标系不同，凑出来的坐标会
        落在不该落的位置，而且看不出来。
        """
        box = artifact.bounding_box_json or {}
        normalized = box.get("normalized")
        if not isinstance(normalized, (list, tuple)) or len(normalized) != 4:
            raise CustomException(
                msg=f"选区 #{index} 所选的图层没有归一化位置框，无法作为选区，请改为自行划框",
                code=400,
            )
        return [int(value) for value in normalized]

    @staticmethod
    def _check_bbox(bbox: list[int], marker: ImageWorkbenchModeMarkerSchema, index: int) -> None:
        """坐标校验：区间内的整数、非零面积、左上点小于右下点。

        区间取自**声明**（``marker.range``）而不是写死的数字（design D10：未实测的写法与
        规则都收在数据与一处代码里）。
        """
        x1, y1, x2, y2 = bbox
        if any(value < 0 or value >= marker.range for value in bbox):
            raise CustomException(
                msg=f"选区 #{index} 的坐标超出该模型允许的区间（0–{marker.range - 1}），当前为 {x1}, {y1}, {x2}, {y2}",
                code=400,
            )
        if x1 == x2 or y1 == y2:
            raise CustomException(msg=f"选区 #{index} 的面积为零，请重新指定范围", code=400)
        if x1 > x2 or y1 > y2:
            raise CustomException(msg=f"选区 #{index} 的左上点必须小于右下点，当前为 {x1}, {y1} → {x2}, {y2}", code=400)

    async def require_visible_artifact(self, artifact_id: int) -> ImageWorkbenchArtifactModel:
        """按**可见范围**取产物：产物归属的任务不可见即拒绝（spec: 交互编辑与选区）。

        与 ``require_artifact`` 的区别是不限定任务——交互编辑引用的是**别处**的产物，提交时
        还不知道本次任务的标识。越权与不存在同答复，不泄露他人产物的存在性。
        """
        artifact = await self.db.get(ImageWorkbenchArtifactModel, artifact_id)
        if artifact is None:
            raise CustomException(msg="所选产物不存在或不在您的可见范围内", code=404)
        await self.require_job(artifact.job_id)
        return artifact

    async def _save_artifact_refs(self, job_id: int, artifact_ids: set[int]) -> None:
        """把来源产物逐条记进引用表（两类来源合流，去重后一行一张产物）。

        去重由 ``sorted(set)`` 与表的唯一约束**双重**保证：同一产物被同一任务从两类来源
        引用时只落一行（design D6）。
        """
        for artifact_id in sorted(artifact_ids):
            self.db.add(ImageWorkbenchJobArtifactRefModel(job_id=job_id, artifact_id=artifact_id))
        if artifact_ids:
            await self.db.flush()

    @staticmethod
    def _is_active_slot_conflict(exc: CustomException) -> bool:
        """仅将「用户活跃任务槽」唯一约束冲突转成友好提示。

        其他完整性错误仍原样上抛，避免把外键或必填字段问题误报成并发冲突。
        """
        # IntegrityError 可能被 CRUD 包装成 CustomException（带 __cause__），也可能直接抛出
        cause = exc if isinstance(exc, IntegrityError) else exc.__cause__
        if not isinstance(cause, IntegrityError):
            return False
        detail = str(cause.orig or cause).lower()
        return "ux_image_workbench_job_active_owner_id" in detail or "active_owner_id" in detail

    def _launch_after_commit(self, job_id: int) -> None:
        """请求事务提交后再启动后台执行（后台任务用独立会话）。"""

        @event.listens_for(self.db.sync_session, "after_commit", once=True)
        def _launch_on_commit(_session) -> None:
            start_job_engine(job_id)

    async def _require_usable_model(self, model_pk: int) -> tuple[ImageWorkbenchModelModel, ImageWorkbenchServiceModel]:
        """模型可用性：模型启用 且 所属服务启用 且 服务已配置凭证（design D8）。"""
        model = await self.db.get(ImageWorkbenchModelModel, model_pk)
        if model is None or model.is_deleted or model.status != 0:
            raise CustomException(msg="该绘图模型不存在或已停用，请重新选择", code=400)
        service = await self.db.get(ImageWorkbenchServiceModel, model.service_id)
        if service is None or service.is_deleted or service.status != 0:
            raise CustomException(msg="该绘图模型所属的绘图服务已停用，请重新选择", code=400)
        if not service.api_key_enc:
            raise CustomException(msg="该绘图服务尚未配置调用凭证，请联系管理员", code=400)
        return model, service

    @staticmethod
    async def _resolve_reference_images(reference_images: list[str], capability: ImageWorkbenchCapabilitySchema) -> tuple[list[str], dict[str, Any]]:
        """校验参考图（支持与否、张数上限、路径与内容），并算出本次请求的**事实**。

        事实集合是封闭的（design D2）：``reference_count``（张数）与
        ``reference_has_alpha``（是否**全部**带透明通道；无参考图时为假）。参数的条件
        依赖只能引用这两项——清单里的 ``background`` 正是靠它们表达「参考图恰好一张
        且该图自带透明通道」。
        """
        reference = capability.reference
        if not reference_images:
            return [], {"reference_count": 0, "reference_has_alpha": False}
        if not reference.supported:
            raise CustomException(msg="该绘图模型不支持参考图，请移除参考图后重试", code=400)
        if len(reference_images) > reference.max_images:
            raise CustomException(
                msg=f"参考图最多 {reference.max_images} 张，当前 {len(reference_images)} 张",
                code=400,
            )
        # 存在性与越界校验（越界即拒绝，避免借参考图字段读取服务器上的任意文件）
        resolved = resolve_reference_files(reference_images)
        has_alpha = True
        for path in resolved:
            validate_reference_image_file(path)
            # 透明通道只做头部解析（design D9），不引入图像处理依赖
            has_alpha = has_alpha and detect_alpha_file(path)
        return reference_images, {"reference_count": len(reference_images), "reference_has_alpha": has_alpha}

    async def _check_concurrency(self) -> None:
        """并发闸门：该用户已有进行中的任务即拒绝（spec: generation / 并发闸门）。"""
        user_id = self.auth.user.id
        if not user_id:
            return
        active = await self.crud.count_active_by_user(user_id)
        if active >= 1:
            raise CustomException(msg="您有正在进行的绘图任务，请等待其结束或取消后再提交", code=400)

    # ─────────────────────────── 查询 ───────────────────────────

    async def require_job(self, id: int) -> ImageWorkbenchJobModel:
        """按可见范围取任务；不可见与不存在同答复（不泄露他人任务的存在性）。"""
        job = await self.crud.get(id=id)
        if job is None:
            raise CustomException(msg="绘图任务不存在或不在您的可见范围内", code=404)
        return job

    async def page(
        self,
        page_no: int,
        page_size: int,
        search: ImageWorkbenchJobQueryParam | None = None,
        order_by: list[dict] | None = None,
    ) -> PageResultSchema[ImageWorkbenchJobOutSchema]:
        result = await self.crud.page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=order_by or [{"id": "desc"}],
            search=search_to_dict(search),
        )
        jobs = list(result.items or [])
        context = await self._out_context(jobs)
        result.items = [await self._to_out(obj, context=context) for obj in jobs]  # type: ignore[assignment]
        return result

    async def detail(self, id: int) -> ImageWorkbenchJobOutSchema:
        job = await self.require_job(id)
        return await self._to_out(job)

    # ────────────────────────── 产物 ───────────────────────────

    @staticmethod
    def _artifact_sort_key(artifact: ImageWorkbenchArtifactModel) -> tuple[int, int]:
        """产物顺序：底图在最前，其余按层级升序（跨库一致的确定性顺序）。

        不能直接 ``ORDER BY z_index``——``NULL`` 在 PostgreSQL 里默认排在最后，
        MySQL / SQLite 里排在最前，同一个任务在不同库上会呈现不同的叠放顺序。
        """
        return (artifact.z_index is not None, artifact.z_index if artifact.z_index is not None else 0)

    async def artifacts_of(self, job_ids: list[int]) -> dict[int, list[ImageWorkbenchArtifactModel]]:
        """批量取产物（列表页一次查完，避免逐行 N+1），按任务分组且已排序。"""
        if not job_ids:
            return {}
        rows = (await self.db.execute(select(ImageWorkbenchArtifactModel).where(ImageWorkbenchArtifactModel.job_id.in_(job_ids)))).scalars().all()
        grouped: dict[int, list[ImageWorkbenchArtifactModel]] = {}
        for row in rows:
            grouped.setdefault(row.job_id, []).append(row)
        for items in grouped.values():
            items.sort(key=self._artifact_sort_key)
        return grouped

    async def require_artifact(self, job_id: int, artifact_id: int) -> ImageWorkbenchArtifactModel:
        """按可见范围取任务后，再确认产物属于该任务（不跨任务取图）。"""
        await self.require_job(job_id)
        artifact = await self.db.get(ImageWorkbenchArtifactModel, artifact_id)
        if artifact is None or artifact.job_id != job_id:
            raise CustomException(msg="该产物不存在或不属于此绘图任务", code=404)
        return artifact

    async def _out_context(self, jobs: list[ImageWorkbenchJobModel] | None = None) -> dict[str, Any]:
        """出参的批量上下文：名称映射 + 默认存储节点 + 产物，避免逐行 N+1。"""
        service_names = {row[0]: row[1] for row in (await self.db.execute(select(ImageWorkbenchServiceModel.id, ImageWorkbenchServiceModel.name))).all()}
        model_names = {row[0]: row[1] for row in (await self.db.execute(select(ImageWorkbenchModelModel.id, ImageWorkbenchModelModel.display_name))).all()}
        try:
            source = await StorageNodeService(AuthSchema(), self.db).get_active_source()
        except Exception:  # noqa: BLE001 — 未配置存储源不该让历史列表整体 500
            source = None
        artifacts = await self.artifacts_of([job.id for job in jobs or []])
        return {"service_names": service_names, "model_names": model_names, "source": source, "artifacts": artifacts}

    async def _to_out(self, job: ImageWorkbenchJobModel, context: dict[str, Any] | None = None) -> ImageWorkbenchJobOutSchema:
        ctx = context if context is not None else await self._out_context([job])
        artifacts = [_artifact_out(ctx["source"], artifact) for artifact in ctx["artifacts"].get(job.id, [])]
        # 列表用底图作缩略：底图没有层级（z_index IS NULL），它永远是排序后的第一张
        base = next((item for item in artifacts if item.is_base), None)
        return ImageWorkbenchJobOutSchema(
            id=job.id,
            uuid=job.uuid,
            created_time=job.created_time,
            updated_time=job.updated_time,
            is_deleted=job.is_deleted,
            deleted_time=job.deleted_time,
            created_id=job.created_id,
            service_id=job.service_id,
            service_name=ctx["service_names"].get(job.service_id),
            model_pk=job.model_pk,
            model_id=job.model_id,
            model_display_name=ctx["model_names"].get(job.model_pk),
            prompt=job.prompt,
            prompt_final=job.prompt_final,
            selections=self._selections_of(job),
            params=job.params_json,
            reference_images=list(job.reference_images_json or []),
            status=job.status,
            status_label=status_label(job.status),
            error=job.error,
            usage=job.usage_json,
            elapsed_ms=job.elapsed_ms,
            # 停用 / 删除的模型不影响历史展示（名称取自裸查，不走软删过滤）
            artifacts=artifacts,
            thumbnail_url=base.image_url if base else None,
            started_at=job.started_at,
            finished_at=job.finished_at,
        )

    @staticmethod
    def _selections_of(job: ImageWorkbenchJobModel) -> list[ImageWorkbenchSelectionOutSchema]:
        """任务行上的选区快照 → 出参（历史行没有这一列时为 None，归一为空列表）。"""
        return [ImageWorkbenchSelectionOutSchema.model_validate(item) for item in (job.selections_json or [])]

    # ─────────────────────────── 取消 / 重试 ──────────────────────────

    async def cancel(self, id: int) -> None:
        """取消进行中的任务：直接落终态 + 中断在途执行（design D6）。

        取消**立即**释放该用户的并发额度、界面立即停止等待；引擎在收尾时读到
        ``cancelled`` 便不再覆盖（取消优先于成功）。取消语义为「放弃等待」，不保证
        上游不产生费用——接口返回与前端确认框都写明这一点。
        """
        job = await self.require_job(id)
        if job.status not in ACTIVE_STATUSES:
            raise CustomException(msg=f"当前状态（{status_label(job.status)}）不可取消", code=400)
        job.status = STATUS_CANCELLED
        job.active_owner_id = None
        job.finished_at = datetime.now(UTC)
        job.error = None
        await self.db.flush()
        request_cancel(id)
        await push_job_progress(id, job.created_id, status=STATUS_CANCELLED, message="任务已取消")

    async def retry(self, id: int) -> ImageWorkbenchJobOutSchema:
        """以**原任务的快照**新建任务（spec: generation / 失败与手动重试，design D11）。

        原失败记录**原样保留**（MUST NOT 覆盖或删除）；重试同样受并发闸门约束。

        重试的语义是「把当初那次请求再发一次」，而当初真正发出去的文本就是 ``prompt_final``
        ——因此它**原样复用**，不拿原话按当前声明重新渲染：标记写法一旦被改过，重渲染出的
        就不再是当初那次请求了，这正是 D5 要保留快照所防的事。

        **不得沿用「只取原话与少数字段」的老路径**：含选区的任务若只取 ``job.prompt``，
        重试发出去的就是仍带 ``区域1`` 占位符、却没有任何坐标的文本——轻则被新校验拒掉
        （用户看到一个「重试不了」的失败任务），重则把未渲染的提示词发到了上游。

        **参数走的是与首次提交完全相同的一套校验与补齐规则**：把原任务的参数快照当作
        「用户显式提交的参数」重新提交，不做任何绕过。因此清单改过之后（原快照含已不
        存在的参数、或已不可用的取值）重试会被**拒绝并说明原因**，而不是把旧参数静默
        发给上游。
        """
        job = await self.require_job(id)
        if job.status not in TERMINAL_STATUSES:
            raise CustomException(msg="仅已结束的任务可重试，进行中的任务请先取消", code=400)
        model, service = await self._require_usable_model(job.model_pk)
        return await self._create(
            model=model,
            service=service,
            capability=parse_capability(model.capability_json),
            prompt=job.prompt,
            # 原样复用快照：原话只用于展示，不参与重试
            prompt_final=job.prompt_final,
            params=dict(job.params_json or {}),
            reference_images=list(job.reference_images_json or []),
            selections=self._selections_of(job),
            ref_artifact_ids=set(await self.referent_artifact_ids(job.id)),
        )

    # ───────────────────────── 引用关系 ───────────────────────────

    async def referent_artifact_ids(self, job_id: int) -> list[int]:
        """某任务引用过的全部产物标识（重试时原样继承来源引用）。"""
        stmt = select(ImageWorkbenchJobArtifactRefModel.artifact_id).where(ImageWorkbenchJobArtifactRefModel.job_id == job_id)
        return list((await self.db.execute(stmt)).scalars().all())

    async def referencing_jobs(self, artifact_ids: list[int], *, exclude_job_ids: set[int] | None = None) -> list[ImageWorkbenchJobModel]:
        """按产物**反查**引用它的绘图任务（只统计其它任务）。

        删除保护是热路径，这条查询靠关联表的 ``artifact_id`` 索引一次解决；JSON 列存引用
        虽省一张表，但只能全表扫（design D6）。软删的任务不算引用者——它已经不属于任何人
        的可见范围，继续拿它挡删除只会让用户无从下手。
        """
        if not artifact_ids:
            return []
        stmt = (
            select(ImageWorkbenchJobModel)
            .join(ImageWorkbenchJobArtifactRefModel, ImageWorkbenchJobArtifactRefModel.job_id == ImageWorkbenchJobModel.id)
            .where(ImageWorkbenchJobArtifactRefModel.artifact_id.in_(artifact_ids))
            .where(ImageWorkbenchJobModel.is_deleted == False)  # noqa: E712
            .distinct()
            .order_by(ImageWorkbenchJobModel.id)
        )
        if exclude_job_ids:
            stmt = stmt.where(ImageWorkbenchJobModel.id.not_in(exclude_job_ids))
        return list((await self.db.execute(stmt)).scalars().all())

    # ───────────────────────── 删除 / 下载 ───────────────────────────

    async def delete(self, ids: list[int]) -> None:
        """删除已结束的任务：软删记录 + **真删**存储节点上的全部产物文件（design D8/D7）。

        进行中的任务 MUST NOT 可删除；任一产物文件删除失败则整体失败（不留「记录没了
        文件还在」的悬空产物）。产物行随后一并**移除**——先删文件再删行，中途失败时行
        还在，用户可重试删除。

        **引用保护**：产物被**其它**绘图任务用作来源时拒绝删除，并列出引用它的任务。两类
        来源（选区来源与画面来源）一视同仁——从删除保护的角度它们没有区别。批量删除时
        「其它任务」指**本批之外**的任务：同一批里的任务互相引用不构成阻碍，否则用户永远
        删不掉一组彼此关联的任务。
        """
        if not ids:
            raise CustomException(msg="删除失败，删除对象不能为空", code=400)
        for id_ in ids:
            job = await self.require_job(id_)
            if job.status not in TERMINAL_STATUSES:
                raise CustomException(msg=f"任务 #{id_} 正在进行中，请先取消后再删除", code=400)
        artifacts = await self.artifacts_of(ids)
        artifact_ids = [artifact.id for items in artifacts.values() for artifact in items]
        referencing = await self.referencing_jobs(artifact_ids, exclude_job_ids=set(ids))
        if referencing:
            refs = "、".join(f"#{job.id}" for job in referencing)
            raise CustomException(
                msg=f"删除被拒绝：这些任务的产物正被其它绘图任务作为来源引用（{refs}），请先删除引用它们的任务",
                code=400,
            )
        for items in artifacts.values():
            for artifact in items:
                await delete_artifact(self.db, artifact.file_path)
        await self.crud.delete(ids=ids)
        # 先清引用行再删产物行：清掉「本批任务作为引用方」的那些行，以及指向本批产物的残留行
        # （例如引用方已被软删）。任务行是软删的，不显式清就没人会清。
        await self._remove_artifact_refs(ids, artifact_ids)
        await self._remove_artifact_rows(ids)

    async def _remove_artifact_rows(self, job_ids: list[int]) -> None:
        """移除产物行（硬删：记录随任务消失，不留在表里当悬空行）。"""
        await self.db.execute(delete(ImageWorkbenchArtifactModel).where(ImageWorkbenchArtifactModel.job_id.in_(job_ids)))
        await self.db.flush()

    async def _remove_artifact_refs(self, job_ids: list[int], artifact_ids: list[int]) -> None:
        """移除引用行：本批任务作为引用方的，以及指向本批产物的。

        引用行只回答「谁用过谁」，任务与产物都不在了它就再无意义。留着它的直接后果是**删除
        保护静默失效**——一张早已被删掉的产物仍被标成「被引用」。
        """
        conditions = [ImageWorkbenchJobArtifactRefModel.job_id.in_(job_ids)]
        if artifact_ids:
            conditions.append(ImageWorkbenchJobArtifactRefModel.artifact_id.in_(artifact_ids))
        await self.db.execute(delete(ImageWorkbenchJobArtifactRefModel).where(or_(*conditions)))
        await self.db.flush()

    async def download(self, id: int, artifact_id: int) -> tuple[str, str, bool]:
        """下载**某一张**产物，返回 ``(本机绝对路径, 下载文件名, 是否为需清理的临时文件)``。"""
        job = await self.require_job(id)
        artifact = await self.require_artifact(id, artifact_id)
        abs_path, is_temp = await prepare_artifact_download(self.db, artifact.file_path)
        return abs_path, f"{job.uuid}-{Path(artifact.file_path).name}", is_temp


def _artifact_out(source: Any, artifact: ImageWorkbenchArtifactModel) -> ImageWorkbenchArtifactOutSchema:
    """产物 ORM → 出参（含本地存储节点下的直接访问地址）。"""
    return ImageWorkbenchArtifactOutSchema(
        id=artifact.id,
        z_index=artifact.z_index,
        is_base=artifact.z_index is None,
        name=artifact.name,
        description=artifact.description,
        bounding_box=artifact.bounding_box_json,
        file_path=artifact.file_path,
        image_url=artifact_url(source, artifact.file_path),
        image_size=artifact.image_size,
        output_format=artifact.output_format,
    )
