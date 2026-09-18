"""菜单 / 权限种子与存量库补种测试（任务 5.1~5.3）

- 5.1 菜单树：一级「绘图工作台」+ 两个子菜单 + design D9 的全部按钮权限码，
      组件名与 `component_path` 与契约一致
- 5.2 种子：火山方舟服务（凭证为空）+ pro / lite 两个能力声明
- 5.3 存量库补种：缺失时补齐、重复执行不产生重复菜单
"""

import json
import sqlite3
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config.setting import settings
from app.scripts.initialize import InitializeData

# design D9 的全部权限码（后端 Security 声明、菜单种子、前端按钮指令三处必须一致）
_EXPECTED_PERMISSIONS = {
    "image_workbench:service:query",
    "image_workbench:service:add",
    "image_workbench:service:edit",
    "image_workbench:service:delete",
    "image_workbench:job:query",
    "image_workbench:job:create",
    "image_workbench:job:delete",
    "image_workbench:job:download",
}

_MENU_ROUTE_NAMES = ("ImageWorkbench", "ImageWorkbenchJob", "ImageWorkbenchConfig")


def _rows(sql: str, params: tuple = ()) -> list[tuple]:
    conn = sqlite3.connect(settings.DATABASE_NAME)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


# ─────────────────────── 5.1 菜单树与权限码 ───────────────────────


def test_menu_tree_contains_both_pages_and_all_permissions(test_client: TestClient) -> None:
    """菜单树：一级菜单下挂工作台与绘图服务两个子菜单。"""
    rows = _rows("select id, name, route_name, route_path, component_path, keep_alive, parent_id from sys_menu where route_name in (?, ?, ?)", _MENU_ROUTE_NAMES)
    by_route = {row[2]: row for row in rows}
    assert set(by_route) == set(_MENU_ROUTE_NAMES), f"菜单树缺失: {set(_MENU_ROUTE_NAMES) - set(by_route)}"

    root = by_route["ImageWorkbench"]
    assert root[3] == "/image-workbench"
    assert root[6] is None, "一级菜单不应有父节点"
    workspace = by_route["ImageWorkbenchJob"]
    assert workspace[3] == "job" and workspace[4] == "image_workbench/job/index"
    assert workspace[6] == root[0], "工作台必须是绘图工作台一级菜单的子项"
    config = by_route["ImageWorkbenchConfig"]
    assert config[3] == "config" and config[4] == "image_workbench/config/index"
    assert config[6] == root[0]

    # KeepAlive 与工作栏按**组件名**匹配：keep_alive 必须打开
    assert workspace[5] == 1 and config[5] == 1


def test_menu_buttons_cover_exactly_the_d9_permission_codes(test_client: TestClient) -> None:
    """按钮权限码与 design D9 逐条一致（后端 Security 声明、菜单种子、前端指令三处同字面量）。"""
    rows = _rows(
        "select permission from sys_menu where permission like 'image_workbench:%' and type = 3",
    )
    seeded = {row[0] for row in rows}
    assert seeded == _EXPECTED_PERMISSIONS, f"多: {seeded - _EXPECTED_PERMISSIONS}；少: {_EXPECTED_PERMISSIONS - seeded}"


# ─────────────────────── 5.2 火山方舟能力声明预置 ───────────────────────


def _seeded_models() -> dict[str, dict[str, Any]]:
    rows = _rows(
        """
        select m.model_id, m.display_name, m.capability_json, m.status
        from image_workbench_model m
        join image_workbench_service s on s.id = m.service_id
        where s.name = '火山方舟 Agent Plan'
        """,
    )
    return {row[0]: {"display_name": row[1], "capability": json.loads(row[2]), "status": row[3]} for row in rows}


def test_seeded_service_has_no_credential(test_client: TestClient) -> None:
    """预置的火山方舟服务凭证为空（填了凭证才可能出现在工作台可选模型中）。"""
    rows = _rows("select name, dialect, base_url, api_key_enc, status from image_workbench_service where name = '火山方舟 Agent Plan'")
    assert len(rows) == 1, "应预置且仅预置一条火山方舟 Agent Plan 服务"
    name, dialect, base_url, api_key_enc, status = rows[0]
    assert dialect == "ark"
    # base_url 填完整 API 前缀（含 /api/plan/v3，ADR-0005）
    assert base_url == "https://ark.cn-beijing.volces.com/api/plan/v3"
    assert api_key_enc is None, "种子 MUST NOT 含任何调用凭证"
    assert status == 0


def _param(capability: dict[str, Any], key: str) -> dict[str, Any]:
    return next(item for item in capability["params"] if item["key"] == key)


def test_seeded_capabilities_parse_and_match_upstream_limits(test_client: TestClient) -> None:
    """预置的两行都能被新 schema 解析，且逐模型数值各按实际（任务 5.1）。"""
    from app.modules.image_workbench.config.capability import ImageWorkbenchCapabilitySchema

    models = _seeded_models()
    assert set(models) == {"doubao-seedream-5-0-pro", "doubao-seedream-5-0-lite"}
    parsed = {key: ImageWorkbenchCapabilitySchema.model_validate(model["capability"]) for key, model in models.items()}

    # 参考图上限：pro 10 / lite 14（lite 由 10 修正为上游文档的 14）
    assert parsed["doubao-seedream-5-0-pro"].reference.max_images == 10
    assert parsed["doubao-seedream-5-0-lite"].reference.max_images == 14

    # 尺寸档位各自反映该模型的实际支持
    assert _param(models["doubao-seedream-5-0-pro"]["capability"], "size")["allowed"] == ["1K", "1.5K", "2K"]
    assert _param(models["doubao-seedream-5-0-lite"]["capability"], "size")["allowed"] == ["2K", "3K", "4K"]

    # 水印：上游默认加水印，预置必须显式 false（ADR-0008），且备注里写明这件事
    for model in models.values():
        assert _param(model["capability"], "watermark")["default"] is False
    remarks = _rows(
        "select m.remark from image_workbench_model m join image_workbench_service s on s.id = m.service_id where s.name = '火山方舟 Agent Plan'"
    )
    assert len(remarks) == 2
    for row in remarks:
        assert "上游默认加水印" in (row[0] or ""), "备注必须写明上游默认是加水印"


def test_seeded_capabilities_mark_measured_values_as_measured(test_client: TestClient) -> None:
    """图层拆分的实测值与 background 的文档字符串域各按其证据标来源。"""
    pro = _seeded_models()["doubao-seedream-5-0-pro"]["capability"]
    assert _param(pro, "layer_decomposition")["source"] == "measured"
    background = _param(pro, "background")
    assert background["source"] == "documented"
    assert background["control"] == "select"
    assert background["allowed"] == ["opaque", "transparent"]
    assert background["default"] == "opaque"
    for key in ("size", "output_format", "watermark", "response_format", "optimize_prompt_options.mode"):
        assert _param(pro, key)["source"] == "documented"

    # 两个参数都带「只在本次请求的输入满足条件时可用」的依赖（design D2）
    assert _param(pro, "layer_decomposition")["when"] == {"all": [{"request": "reference_count", "eq": 1}]}
    assert _param(pro, "background")["when"]["all"][0] == {"param": "output_format", "not_in": ["jpeg"]}


def test_seeded_models_declare_their_own_modes_and_options(test_client: TestClient) -> None:
    """逐模型差异：图层拆分方式与 fast 档只属 pro；lite 不声明即不会发出这两个参数。"""
    models = _seeded_models()
    pro, lite = models["doubao-seedream-5-0-pro"]["capability"], models["doubao-seedream-5-0-lite"]["capability"]

    assert [mode["key"] for mode in pro["modes"]] == ["layer_decomposition", "interactive_edit"]
    assert pro["modes"][0]["trigger"] == {"param": "layer_decomposition", "eq": True}
    assert lite["modes"] == [], "lite 不支持图层拆分，不得声明该方式"

    lite_keys = {item["key"] for item in lite["params"]}
    assert "layer_decomposition" not in lite_keys and "background" not in lite_keys
    assert _param(lite, "optimize_prompt_options.mode")["allowed"] == ["standard"], "fast 档不支持 lite"
    assert _param(pro, "optimize_prompt_options.mode")["allowed"] == ["standard", "fast"]

    # 同一参数在图层拆分方式下的取值域与默认值不同（design D3 的 overrides）
    assert _param(pro, "size")["overrides"]["layer_decomposition"] == {"allowed": ["1K", "1.5K", "2K", "auto"], "default": "auto"}


# ─────────────────────── 5.3 存量库幂等补种 ───────────────────────


def _menu_subtree_count() -> int:
    placeholders = ",".join("?" for _ in _MENU_ROUTE_NAMES)
    return _rows(f"select count(*) from sys_menu where route_name in ({placeholders})", _MENU_ROUTE_NAMES)[0][0]


def _delete_menu_subtree() -> None:
    """模拟存量库：种子库里还没有这棵子树。"""
    conn = sqlite3.connect(settings.DATABASE_NAME)
    try:
        conn.execute("delete from sys_menu where permission like 'image_workbench:%'")
        placeholders = ",".join("?" for _ in _MENU_ROUTE_NAMES)
        conn.execute(f"delete from sys_menu where route_name in ({placeholders})", _MENU_ROUTE_NAMES)
        conn.commit()
    finally:
        conn.close()


async def test_menu_merge_fills_missing_subtree_and_stays_idempotent(test_client: TestClient) -> None:
    """存量库补种：缺失时按 route_name 补齐，连续两次不产生重复菜单（任务 5.3）。"""
    _delete_menu_subtree()
    assert _menu_subtree_count() == 0

    merge = InitializeData()._InitializeData__ensure_seed_menu_merged  # noqa: SLF001 — 直测补种逻辑本身
    maker = async_sessionmaker(create_async_engine(f"sqlite+aiosqlite:///{settings.DATABASE_NAME}"), expire_on_commit=False)
    async with maker() as db, db.begin():
        await merge(db, route_name="ImageWorkbench", title="绘图工作台")
    after_first = _menu_subtree_count()
    assert after_first == len(_MENU_ROUTE_NAMES)

    # 再跑一次（等价于再重启一次后端）
    async with maker() as db, db.begin():
        await merge(db, route_name="ImageWorkbench", title="绘图工作台")
    assert _menu_subtree_count() == after_first, "重复执行不得产生重复菜单"

    # 补齐的子树权限码完整
    rows = _rows("select permission from sys_menu where permission like 'image_workbench:%' and type = 3")
    assert {row[0] for row in rows} == _EXPECTED_PERMISSIONS


def test_seeded_pro_model_declares_interactive_edit_marker(test_client: TestClient) -> None:
    """pro 预置交互编辑（固有能力位）及其标记语法，工作台的画布入口据此出现。

    交互编辑在上游没有对应参数，它靠提示词里的坐标标记表达——因此标记的占位形态、坐标
    标记写法与坐标区间只能来自声明（spec: service-config / 绘图模型能力声明）。
    坐标标记语法**尚未在 /api/plan/v3 上实测**，因此来源标 ``documented``。
    """
    from app.modules.image_workbench.config.capability import ImageWorkbenchCapabilitySchema

    pro = ImageWorkbenchCapabilitySchema.model_validate(_seeded_models()["doubao-seedream-5-0-pro"]["capability"])
    mode = pro.coordinate_mode
    assert mode is not None and mode.key == "interactive_edit"
    assert mode.trigger is None, "交互编辑是固有方式，不由任何参数触发"
    assert mode.marker.placeholder == "区域{n}"
    assert mode.marker.template == "<bbox>{x1} {y1} {x2} {y2}</bbox>"
    assert mode.marker.range == 1000, "坐标归一化到 1000×1000，取值 0–999"

    # 图层拆分与坐标无关，不要求它声明任何标记语法
    layer_mode = next(item for item in pro.modes if item.key == "layer_decomposition")
    assert layer_mode.marker is None
