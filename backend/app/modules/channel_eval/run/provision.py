"""指定渠道（pin）自动准备（设计 D3，ADR-0001）

new-api v1.0.0-rc.21+ 原生支持「指定渠道」：管理员用户名下的令牌，
请求头 ``Authorization: Bearer <key>-<渠道ID>`` 即把请求 pin 到该渠道
（``middleware/auth.go`` 先剥离 ``sk-`` 前缀再按 ``-`` 切分：``parts[0]`` 为真 key、
``parts[1]`` 为渠道ID；非管理员用户 403「普通用户不支持指定渠道」；
``middleware/distributor.go`` pin 后直取渠道，仅校验存在+启用，不走分组路由、
不做模型列表校验；``controller/relay.go`` pin 时禁止重试 → 归因 100% 准确）。

每次运行创建时经平台管理接口创建 **1 个专用管理员令牌**（命名约定
``eval-run{运行ID}``，group=default，属平台管理用户），本运行全部
「渠道 × 模型」组合复用它并 pin 到各自渠道；令牌明文经密钥接口取回
（列表/查询接口只回脱敏值）后加密存 ``channel_eval_run.eval_token_*``。
对 apiworld 的写操作仅此一项（ADR-0001 白名单），永不触碰优先级/权重/启停/模型映射；
专用令牌与日常测试令牌隔离，日志按令牌名过滤互不污染。

基准渠道（平台 type=user）无内部线路可 pin，直接以平台测试凭证发请求（隐式渠道）。
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import logger
from app.modules.channel_eval.platform.model import ChannelEvalPlatformModel
from app.modules.channel_eval.run.model import ChannelEvalRunModel
from app.utils.crypto_util import CryptoUtil

from .new_api_client import NewApiClient

# 评估令牌所在分组：default 组必在管理员用户可用组与 GroupRatio 中（new-api 标准配置），
# 且 pin 时分组仅用于用户可用性校验，不参与渠道路由
_EVAL_TOKEN_GROUP = "default"


class ProvisionError(Exception):
    """指定渠道准备失败（含明确原因）"""


def eval_run_token_name(run_id: int) -> str:
    """评估令牌命名约定：eval-run{运行ID}（每次运行 1 个专用管理员令牌）。"""
    return f"eval-run{run_id}"


def build_client(platform: ChannelEvalPlatformModel) -> NewApiClient:
    """按平台凭证构造管理端 NewApiClient（含 New-Api-User 管理用户ID，可选）。"""
    return NewApiClient(
        platform.base_url,
        admin_key=CryptoUtil.decrypt(platform.admin_key_enc) if platform.admin_key_enc else None,
        admin_user_id=platform.admin_user_id,
    )


async def ensure_eval_token(db: AsyncSession, platform: ChannelEvalPlatformModel, run: ChannelEvalRunModel) -> str:
    """确保本运行专用评估令牌就绪，返回明文 key（立即加密写回 run 行）。

    幂等：令牌已登记（``eval_token_key_enc`` 非空）直接复用；远端按名存在
    （如上次准备中断留下孤儿令牌）同样复用，不重复创建。``run`` 必须在
    ``db`` 会话内（未提交事务），写回随调用方事务一并提交。
    """
    name = eval_run_token_name(run.id)
    client = build_client(platform)
    try:
        # 1. 先查后建（远端是事实来源）
        tokens = await client.list_tokens()
        token = next((t for t in tokens if t.get("name") == name), None)
        created = False
        if token is None:
            token = await client.create_eval_token(name=name, group=_EVAL_TOKEN_GROUP)
            created = True
            logger.info("渠道评估：运行 {} 已创建专用评估令牌 {}", run.id, name)
        # 2. 明文 key：列表/查询接口只回脱敏值（脱敏值发请求 401），必须经密钥接口取回
        key = str(token.get("key") or "")
        if not key or "*" in key:
            key = await client.get_token_key(int(token.get("id") or 0))
        # 3. 加密写回 run 行（复用路径同样更新，保证密文与远端一致）
        run.eval_token_id = int(token.get("id") or 0) or None
        run.eval_token_key_enc = CryptoUtil.encrypt(key)
        await db.flush()
        return key
    except ProvisionError:
        raise
    except Exception as e:  # noqa: BLE001
        raise ProvisionError(f"评估令牌准备失败: {e}") from e


async def resolve_eval_token_key(db: AsyncSession, platform: ChannelEvalPlatformModel, run: ChannelEvalRunModel) -> str:
    """解析本运行评估令牌明文：已登记直接解密返回；否则先查后建（引擎兜底路径）。"""
    if run.eval_token_key_enc:
        key = CryptoUtil.decrypt(run.eval_token_key_enc)
        if key and "*" not in key:
            return key
    return await ensure_eval_token(db, platform, run)
