"""绘图工作台 - 绘图任务 Pydantic 模型（设计 D5/D9/D11）

提交入参只做**浅校验**（非空、长度、形态）；参数是否在许可范围内取决于所选模型的
**能力声明**（存库可配），必须查库比对，因此落在服务层（``service.py``），不在这里写死。

提交契约是**开放参数字典**（D5）：``params`` 只装用户改动过的键，未提交的由服务端按
清单补默认值。参数键就是上游字段路径（点号表示嵌套），服务端不认「本地名 → 上游名」
的映射表。

``params_json`` 是**完整且已解析的参数集快照**——它等于实际发给上游的参数集（含补齐
的默认值）。参考图不是参数（D4），单独存 ``reference_images_json``：它们由用户经模块
专属 ``POST /image_workbench/jobs/reference-images/upload`` 上传后已落在
``static/upload`` 下，执行时按接入方式读取上行，不重复拷贝。
"""

from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.base_schema import BaseQueryParam, BaseSchema, DateTimeStr, UploadResponseSchema

from ..config.capability import ImageWorkbenchCapabilitySchema

# 任务状态取值（设计 D2 的枚举；不含过渡态——取消直接落终态 ``cancelled``，
# 使「取消即释放并发额度」与「取消后 MUST 转入已取消状态」两条同时成立）
STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"

TERMINAL_STATUSES: frozenset[str] = frozenset({STATUS_SUCCESS, STATUS_FAILED, STATUS_CANCELLED})
"""终态集合：删除只允许作用于终态任务（spec: generation / 删除绘图任务）。"""

ACTIVE_STATUSES: frozenset[str] = frozenset({STATUS_PENDING, STATUS_RUNNING})
"""进行中集合：并发闸门按它统计（spec: generation / 并发闸门）。"""

_STATUS_LABEL: dict[str, str] = {
    STATUS_PENDING: "等待执行",
    STATUS_RUNNING: "生成中",
    STATUS_SUCCESS: "生成成功",
    STATUS_FAILED: "生成失败",
    STATUS_CANCELLED: "已取消",
}


def status_label(status: str | None) -> str | None:
    """任务状态取值 → 界面显示名。"""
    if not status:
        return None
    return _STATUS_LABEL.get(status, status)


class ImageWorkbenchReferenceUploadSchema(UploadResponseSchema):
    """参考图上传响应（design D9：在原上传响应上补一个透明通道标志）

    ``has_alpha`` 在**上传时**即判定，供前端当场决定「图片透明通道」等参数是否可用，
    而不是等到提交才被拒。
    """

    has_alpha: bool = Field(default=False, description="参考图是否带透明通道")


class ImageWorkbenchJobModelOptionSchema(BaseModel):
    """工作台可选绘图模型（含能力声明，供生成表单联动尺寸/参考图/格式）

    取值条件（设计 D8）：模型启用 **且** 所属服务启用 **且** 服务已配置凭证。
    种子预置的火山方舟服务凭证为空，因此预置完成后不会意外可选。
    """

    id: int = Field(description="绘图模型ID")
    service_id: int = Field(description="所属绘图服务ID")
    service_name: str = Field(description="所属绘图服务名称")
    model_id: str = Field(description="上游模型标识")
    display_name: str = Field(description="工作台展示名")
    capability: ImageWorkbenchCapabilitySchema = Field(description="能力声明")


class ImageWorkbenchSelectionSchema(BaseModel):
    """一个选区：序号 + 位置来源**二选一**（design D3）。

    - ``artifact_id``：点选一个现成的图层，坐标由服务端读该产物的位置框——图层拆分免费
      给出了位置框，用户不必自己划；
    - ``bbox``：用户自己划出的范围，``[左上x, 左上y, 右下x, 右下y]``。

    坐标是**结构化字段**而不是自然语言的一部分：写成结构才能校验（区间、非零面积、序号
    唯一），也不会被用户在文本框里手改坏。区间校验（``0..range-1``）留在服务层——区间
    由所选模型的声明给出，不在这里写死一个数字。
    """

    index: int = Field(..., ge=1, description="选区序号(与提示词里的区域 N 对应)")
    artifact_id: int | None = Field(default=None, ge=1, description="点选图层时的来源产物ID")
    bbox: list[int] | None = Field(default=None, description="自行划框的坐标[左上x, 左上y, 右下x, 右下y]")

    @field_validator("bbox", mode="before")
    @classmethod
    def _check_bbox_shape(cls, value: Any) -> Any:
        """坐标必须是**整数**：``mode="before"`` 是为了在 Pydantic 的宽松转换之前拦下
        ``"120"`` 这类「看起来像整数的东西」——坐标是结构化数据，悄悄替调用方转换只会把
        前端的类型错误藏到坐标已经落库之后。布尔同样不算整数（``True`` 不是 ``1``）。
        """
        if value is None:
            return None
        if not isinstance(value, (list, tuple)) or len(value) != 4:
            raise ValueError("坐标框必须是四个值：左上x、左上y、右下x、右下y")
        if any(isinstance(item, bool) or not isinstance(item, int) for item in value):
            raise ValueError("坐标框的四个值都必须是整数")
        return list(value)

    @model_validator(mode="after")
    def _exactly_one_source(self) -> "ImageWorkbenchSelectionSchema":
        if (self.artifact_id is None) == (self.bbox is None):
            raise ValueError(f"选区 #{self.index} 必须且只能给出一种位置来源：点选图层（artifact_id）或自行划框（bbox）")
        return self


class ImageWorkbenchSelectionOutSchema(BaseModel):
    """一个**已解析**的选区：坐标 + 它取自哪一张产物（design D3/D5）。

    与入参的 ``ImageWorkbenchSelectionSchema`` 刻意分开，因为两者回答的不是同一个问题：

    - 入参表达「位置从哪来」——点选图层或自行划框，**二选一**；
    - 快照表达「解析结果是什么」——坐标**一定有**（点选时由服务端从产物的位置框读出），
      ``artifact_id`` 只是「取自哪一层」的溯源信息，自行划框时为空。

    混用一个模型会让「点选图层」这种情形无法表达：它同时有坐标与来源产物。
    """

    index: int = Field(..., ge=1, description="选区序号(与提示词里的区域 N 对应)")
    bbox: list[int] = Field(description="归一化坐标[左上x, 左上y, 右下x, 右下y]")
    artifact_id: int | None = Field(default=None, description="点选图层时的来源产物ID(自行划框时为空)")


class ImageWorkbenchJobCreateSchema(BaseModel):
    """绘图任务提交参数（设计 D5：开放参数字典）

    ``params`` 只装**用户改动过的**键；未提交的参数由服务端按清单补默认值——两边因此
    不会对「默认值到底是几」产生分歧，也不会有「漏传即得到与预期相反的结果」的坑
    （``watermark`` 上游默认 true、产品默认 false，漏传就是相反的成品）。

    选区与画面来源都是**可选**字段：不传时行为与上一个变更完全一致，未升级的前端不受影响
    （design Migration Plan 的兼容性约定）。
    """

    model_pk: int = Field(..., ge=1, description="绘图模型ID")
    prompt: str = Field(..., min_length=1, max_length=4000, description="提示词(含区域占位符)")
    params: dict[str, Any] = Field(default_factory=dict, description="用户改动过的参数(键为清单参数键，点号表示嵌套)")
    reference_images: list[str] = Field(default_factory=list, max_length=32, description="参考图在本系统内的相对路径数组")
    selections: list[ImageWorkbenchSelectionSchema] = Field(default_factory=list, description="选区(序号与提示词里的区域 N 对应)")
    composition_source_job_id: int | None = Field(
        default=None,
        ge=1,
        description="画面来源：构成这张参考图的那次绘图任务ID(画布不来自产物时缺省)",
    )

    @field_validator("prompt")
    @classmethod
    def _strip_prompt(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("提示词不能为空")
        return value.strip()

    @field_validator("params")
    @classmethod
    def _clean_params(cls, value: dict[str, Any]) -> dict[str, Any]:
        cleaned: dict[str, Any] = {}
        for key, item in (value or {}).items():
            if key is None or not str(key).strip():
                raise ValueError("参数键不能为空")
            cleaned[str(key).strip()] = item
        return cleaned

    @field_validator("reference_images")
    @classmethod
    def _clean_reference_images(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if item and item.strip()]


class ImageWorkbenchArtifactOutSchema(BaseModel):
    """单张产物（设计 D7/D11）。

    ``z_index`` 为 ``None`` 即底图——底图不是图层，用「无层级」而非「层级 0」表达这一点。
    """

    id: int = Field(description="产物ID")
    z_index: int | None = Field(default=None, description="层级(底图为空)")
    is_base: bool = Field(default=False, description="是否为底图")
    name: str | None = Field(default=None, description="图层名称")
    description: str | None = Field(default=None, description="图层描述")
    bounding_box: dict[str, Any] | None = Field(default=None, description="图层在底图中所占的位置")
    file_path: str | None = Field(default=None, description="产物在存储节点中的相对路径")
    image_url: str | None = Field(default=None, description="产物可直接访问地址(本地存储节点时给出)")
    image_size: str | None = Field(default=None, description="产物宽高")
    output_format: str | None = Field(default=None, description="产物格式")


class ImageWorkbenchJobOutSchema(BaseSchema):
    """绘图任务响应模型（列表与详情共用）

    ``prompt`` 与 ``prompt_final`` 是两个形态，**都保留**（design D5）：用户回看历史时看
    自己写的原话，追溯「当时到底发了什么」时看最终提示词。两者相同时由前端只呈现一份。
    """

    service_id: int = Field(description="绘图服务ID")
    service_name: str | None = Field(default=None, description="绘图服务名称")
    model_pk: int = Field(description="绘图模型ID")
    model_id: str = Field(description="模型标识快照")
    model_display_name: str | None = Field(default=None, description="绘图模型展示名")
    prompt: str = Field(description="提示词(用户原话)")
    prompt_final: str = Field(description="最终提示词(实际发给上游的文本，含坐标标记)")
    selections: list[ImageWorkbenchSelectionOutSchema] = Field(default_factory=list, description="选区快照(含来源产物标识)")
    params: dict[str, Any] | None = Field(default=None, description="参数快照(等于实际发给上游的参数集)")
    reference_images: list[str] = Field(default_factory=list, description="参考图在本系统内的相对路径数组")
    status: str = Field(description="状态(pending/running/success/failed/cancelled)")
    status_label: str | None = Field(default=None, description="状态显示名")
    error: str | None = Field(default=None, description="失败原因")
    usage: dict[str, Any] | None = Field(default=None, description="用量明细(上游usage原样+生成张数)")
    elapsed_ms: int | None = Field(default=None, description="任务耗时(毫秒)")
    artifacts: list[ImageWorkbenchArtifactOutSchema] = Field(default_factory=list, description="产物(底图在前，图层按层级升序)")
    thumbnail_url: str | None = Field(default=None, description="列表缩略图地址(取底图)")
    started_at: DateTimeStr | None = Field(default=None, description="执行开始时间")
    finished_at: DateTimeStr | None = Field(default=None, description="执行结束时间")
    created_id: int | None = Field(default=None, description="提交人ID")


class ImageWorkbenchJobQueryParam(BaseQueryParam):
    """绘图任务查询参数（可见范围由数据范围机制在数据层施加，不在此处暴露）"""

    prompt: str | None = Field(default=None, max_length=500, description="提示词", json_schema_extra={"q": "like"})
    model_id: str | None = Field(default=None, max_length=128, description="模型标识", json_schema_extra={"q": "like"})
    status: str | None = Field(default=None, max_length=16, description="状态", json_schema_extra={"q": "eq"})
    service_id: int | None = Field(default=None, ge=1, description="绘图服务ID", json_schema_extra={"q": "eq"})
