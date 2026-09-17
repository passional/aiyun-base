"""绘图工作台 - 绘图任务 Pydantic 模型（设计 D9 API 契约 / D3 能力声明）

提交入参只做**浅校验**（非空、长度、路径形态）；尺寸 / 输出格式 / 参考图是否在
许可范围内取决于所选模型的**能力声明**（存库可配），必须查库比对，因此落在
服务层（``service.py``），不在这里写死。

``params_json`` 是本次参数的**快照**：``size`` / ``output_format`` / ``watermark`` /
``reference_images``（参考图存本系统内的相对路径数组，见 design D2——它们由用户经
模块专属 ``POST /image_workbench/jobs/reference-images/upload`` 上传后已落在
``static/upload`` 下，执行时按接入方式读取
上行，不重复拷贝）。
"""

from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.core.base_schema import BaseQueryParam, BaseSchema, DateTimeStr

from ..config.schema import ImageWorkbenchCapabilitySchema

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


class ImageWorkbenchJobCreateSchema(BaseModel):
    """绘图任务提交参数（一次任务产出一张图）"""

    model_pk: int = Field(..., ge=1, description="绘图模型ID")
    prompt: str = Field(..., min_length=1, max_length=4000, description="提示词")
    size: str | None = Field(default=None, max_length=32, description="尺寸取值(须在该模型能力声明内)")
    output_format: str | None = Field(default=None, max_length=16, description="输出格式(缺省取能力声明默认值)")
    watermark: bool | None = Field(default=None, description="是否加水印(缺省取能力声明的水印默认值)")
    reference_images: list[str] = Field(default_factory=list, max_length=20, description="参考图在本系统内的相对路径数组")

    @field_validator("prompt")
    @classmethod
    def _strip_prompt(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("提示词不能为空")
        return value.strip()

    @field_validator("size", "output_format")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        return value.strip() if value and value.strip() else None

    @field_validator("reference_images")
    @classmethod
    def _clean_reference_images(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if item and item.strip()]


class ImageWorkbenchJobOutSchema(BaseSchema):
    """绘图任务响应模型（列表与详情共用）"""

    service_id: int = Field(description="绘图服务ID")
    service_name: str | None = Field(default=None, description="绘图服务名称")
    model_pk: int = Field(description="绘图模型ID")
    model_id: str = Field(description="模型标识快照")
    model_display_name: str | None = Field(default=None, description="绘图模型展示名")
    prompt: str = Field(description="提示词")
    params: dict[str, Any] | None = Field(default=None, description="参数快照")
    status: str = Field(description="状态(pending/running/success/failed/cancelled)")
    status_label: str | None = Field(default=None, description="状态显示名")
    error: str | None = Field(default=None, description="失败原因")
    usage: dict[str, Any] | None = Field(default=None, description="用量明细(上游usage原样+生成张数)")
    elapsed_ms: int | None = Field(default=None, description="任务耗时(毫秒)")
    image_path: str | None = Field(default=None, description="产物在存储节点中的相对路径")
    image_url: str | None = Field(default=None, description="产物可直接访问地址(本地存储节点时给出)")
    image_size: str | None = Field(default=None, description="产物宽高")
    started_at: DateTimeStr | None = Field(default=None, description="执行开始时间")
    finished_at: DateTimeStr | None = Field(default=None, description="执行结束时间")
    created_id: int | None = Field(default=None, description="提交人ID")


class ImageWorkbenchJobQueryParam(BaseQueryParam):
    """绘图任务查询参数（可见范围由数据范围机制在数据层施加，不在此处暴露）"""

    prompt: str | None = Field(default=None, max_length=500, description="提示词", json_schema_extra={"q": "like"})
    model_id: str | None = Field(default=None, max_length=128, description="模型标识", json_schema_extra={"q": "like"})
    status: str | None = Field(default=None, max_length=16, description="状态", json_schema_extra={"q": "eq"})
    service_id: int | None = Field(default=None, ge=1, description="绘图服务ID", json_schema_extra={"q": "eq"})
