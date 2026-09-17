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


def test_seeded_capabilities_match_upstream_limits(test_client: TestClient) -> None:
    """pro / lite 两个模型的能力声明：尺寸档位分别为 1K/1.5K/2K 与 2K/3K/4K。"""
    models = _seeded_models()
    assert set(models) == {"doubao-seedream-5-0-pro", "doubao-seedream-5-0-lite"}
    assert models["doubao-seedream-5-0-pro"]["capability"]["sizes"] == ["1K", "1.5K", "2K"]
    assert models["doubao-seedream-5-0-lite"]["capability"]["sizes"] == ["2K", "3K", "4K"]
    for model in models.values():
        capability = model["capability"]
        assert capability["output_formats"] == ["png", "jpeg"]
        assert capability["default_output_format"] == "png"
        # 上游默认加水印：预置必须显式 false，否则成品图会被打上「AI 生成」水印
        assert capability["watermark_default"] is False


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
