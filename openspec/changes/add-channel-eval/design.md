# Design: 渠道评估能力（channel-eval）

## Context

动机与范围见 `proposal.md`；行为契约见 `specs/channel-eval/{platform-access,test-run,compare-report}/spec.md`；已定决策见 `docs/adr/0001~0003` 与根目录 `CONTEXT.md`。

工程约束（仓库现状）：
- 后端模块约定：`app/modules/<模块>/<功能>/`（controller/service/crud/model/schema + 模块根 plugin.toml），路由在 `app/api/v1/routers.py` 统一注册，权限三段式 `模块:资源:操作`。
- 可复用基建：`core/sse_manager`（进度推送）、`core/redis_crud`、`utils/crypto_util.CryptoUtil`（凭证加密）、httpx 0.28（外部 HTTP）、loguru、Alembic 迁移 + `backend/sql/*.json` 菜单种子（启动时 initialize.py 自动导入）。
- 前端：`api/module_x/` + `views/module_x/`，CRUD 复用 useTable/FaTable/useCrudDialog 等套件；已有图表组件 `components/charts/fa-dual-bar-compare-chart` 可复用于对比视图。
- 部署形态：docker-compose 单 backend 进程。
- apiworld 为有线上流量的 new-api 站点：只读为主，写操作白名单两项（ADR-0001）。

## Goals / Non-Goals

**Goals:**
- 新模块 `app/modules/channel_eval/`，含 platform / run / report 三个功能子目录与全套 API。
- 异步执行引擎：真实流式请求采集客户端实测，后台日志拉取与 request_id 1:1 配对。
- 分组定向自动准备（幂等），写操作白名单在集成层强制。
- 对比报告 Web 视图 + 自包含 HTML 导出；"评估中心"菜单种子与 RBAC。

**Non-Goals:**
- 定时自动运行（实体预留 `schedule_hint` 类字段，二期接 `core/ap_scheduler`）。
- new-api 自带测渠道接口（路线 A）、优先级/启停的程序化调整（已否决，见 ADR-0001）。
- 多 worker/多实例部署下的任务调度；frontend/app 小程序端；自动结论的评价性措辞。

## Decisions

### D1 模块与数据模型

新建 `app/modules/channel_eval/`，下分 `platform/`、`run/`、`report/` 三个功能（各含 controller/service/crud/model/schema），模块根 `plugin.toml`。数据表（均继承 base_model 的审计字段）：

| 表 | 关键字段 | 说明 |
| --- | --- | --- |
| `channel_eval_platform` | name、type(`admin`/`user`)、base_url、admin_key_enc、test_key_enc、is_baseline、status、remark | 平台；凭证 CryptoUtil 加密，接口只回显脱敏 |
| `channel_eval_provision` | platform_id、channel_id、group_tag、token_id、token_key_enc、status | 分组定向**产物**记录（非渠道配置镜像，不违反 ADR-0003：目录永远实时拉取，本表只存评估系统自己创建的令牌与标签） |
| `channel_eval_run` | platform_id、status(`pending/running/success/failed/cancelled`)、params_json、schedule_hint(预留)、started_at、finished_at、error | params_json 固化：次数/热身/间隔/超时/prompt/异常阈值 |
| `channel_eval_run_combo` | run_id、channel_id、channel_name(快照)、model、planned、success、failed、anomaly | 组合清单即测试集快照；聚合计数冗余便于列表 |
| `channel_eval_sample` | combo_id、seq、is_warmup、request_id、ttft_ms、total_ms、tokens_in、tokens_out、tps、status、error、started_at、be_matched、be_frt_ms、be_use_time_ms、be_tokens_in、be_tokens_out、be_cost、be_channel_id | 单样本：客户端实测 + 配对后台字段（be_*） |
| `channel_eval_report` | name、run_ids(json)、baseline_run_id、note | 报告=选择+备注，不复制测量数据 |

Alembic：一次 `revision` 建全部 6 表；`downgrade` 按外键逆序 drop。菜单种子新增 `backend/sql/` 菜单 JSON（评估中心 + 三个子菜单 + 按钮权限），回滚时删除对应菜单记录。

### D2 new-api 集成层与白名单强约束

`run` 功能内封装 `NewApiClient`（httpx.AsyncClient，base_url+管理员凭证）：只读方法 `list_channels()`、`list_tokens()`、`list_logs(token_name, start_ts, end_ts)`；用户级平台用 `list_self_logs()`。写方法**只暴露两个**：`append_channel_group(channel, group_tag)`（读改写 group 字段，保留原值）、`create_eval_token(name, group)`。白名单不靠纪律而靠接口面：client 不存在其它写方法，代码评审锚点即此文件。

日志字段口径（已对 new-api 源码核实）：`use_time` 秒、`other.frt` 毫秒（首字）、`token_name`、`channel`(渠道 id)、`request_id`、tokens、quota。版本差异降级：缺 `frt`/`request_id` 时后台首字显示"—"、配对退化为"后台无记录"标注。

### D3 分组定向准备（provision）

命名约定 `eval-ch{渠道ID}`。流程：`list_channels` 找到目标渠道 → 分组列表缺标签则 `append_channel_group`；`list_tokens` 找名为 `eval-ch{渠道ID}` 的令牌 → 缺则 `create_eval_token`（令牌明文仅创建响应返回一次，立即加密存 `channel_eval_provision`）。幂等由"先查后建"保证。基准渠道（type=`user`）跳过 provision，直接用平台测试凭证发请求（外部平台视为一条隐式渠道，无定向需求）。

### D4 执行引擎：进程内 asyncio 任务 + Redis 进度 + SSE

- 创建运行→写库 pending→`asyncio` 后台任务执行。任务注册表（内存 dict run_id→Task）支撑取消：取消置 cancelling 标志，任务在组合边界检查并退出。
- 进度（当前组合、完成数）写 Redis，`sse_manager` 推送运行详情页；断线重连以 Redis 进度为准。
- 同平台串行：同平台存在 running/pending 运行时禁止新建（避免日志时间窗交叉污染）。
- 进程重启恢复：启动钩子把 `running/cancelling` 置 `failed`（error=进程重启），已产生样本保留。
- 备选否决：`core/ap_scheduler` 面向定时触发而非交互式长任务；引入独立任务队列超出当前部署形态（单进程）。

### D5 客户端实测与配对

- 每组合：取该渠道 provision 令牌 → 顺序发 N 次（间隔默认 2s）`POST {base}/v1/chat/completions`（`stream:true`、`stream_options.include_usage:true`）。TTFT=首内容 delta 到达；总耗时=至 `[DONE]`；tps=输出 tokens÷(总耗时−TTFT)；request_id 取响应头 `X-Oneapi-Request-Id`。热身（默认 1 次）先打且 `is_warmup=true`。
- 运行结束（含 cancelled/failed 的已测部分）→ 延迟约 5s 待日志落库 → 按 `token_name + 运行时间窗` 分页拉全消耗日志 → 按 `request_id` 配对写 `be_*`；未配对样本 `be_matched=false` 标"后台无记录"。基准渠道走 `list_self_logs` + 时间窗过滤。
- 异常口径：ttft_ms 或 total_ms 超阈值（默认 30s，随 params_json）→ `anomaly` 计入 combo；聚合剔除 warmup+anomaly+失败样本。

### D6 API 契约（前缀 `/api/v1/channel_eval`，均挂 OperationLogRoute + `Security(AuthPermission(...))`）

| 资源 | 路径 | 说明 |
| --- | --- | --- |
| 平台 | `GET/POST /platforms`、`GET/PUT/DELETE /platforms/{id}`、`POST /platforms/{id}/test-connection`、`GET /platforms/{id}/channels` | 权限 `channel_eval:platform:query/add/edit/delete`；channels 实时拉取 |
| 运行 | `GET/POST /runs`、`GET /runs/{id}`、`POST /runs/{id}/cancel`、`GET /runs/{id}/events`(SSE) | 权限 `channel_eval:run:query/add/cancel`；详情含 combos+samples |
| 报告 | `GET/POST /reports`、`GET/PUT/DELETE /reports/{id}`、`GET /reports/{id}/view`、`GET /reports/{id}/export` | 权限 `channel_eval:report:query/add/edit/delete`；view 返回聚合 JSON；export 返回 HTML 附件 |

响应统一 `SuccessResponse/ResponseSchema[T]`；业务错误 `CustomException`。

### D7 前端与报告导出

- `api/module_channel_eval/{platform,run,report}.ts` + `views/channel_eval/{platform,run,report}/`；平台/报告列表用 useTable+FaTable+useCrudDialog 套件；运行创建为向导页（选平台→拉渠道树→逐渠道勾模型→参数表单）；运行详情页 SSE 进度 + 样本明细表；报告视图复用 `fa-dual-bar-compare-chart` 呈现分组条形。i18n zh/en 双份；组件 `defineOptions.name` 与菜单 `route_name` 一致保 KeepAlive。
- HTML 导出：后端 Jinja2 模板渲染，原型为根目录 `model_compare.html` 的重构（保留其信息架构：KPI/分组条形/明细表/交叉对比/异常清单/结论，版式与图表绘制可重构），数据 JSON 内嵌、纯 HTML/CSS/JS 零外部依赖、离线可开。

## Risks / Trade-offs

- [apiworld 的 new-api 版本字段差异（frt/request_id 缺失）] → D2 降级路径；首次联调用一条真实日志核实字段，缺失则对应列显示"—"。
- [写操作越界风险（线上平台）] → 集成层接口面白名单（D2），无其它写方法可供误调；评审锚点固定。
- [进程重启丢执行中运行] → 启动钩子置 failed 并保留样本（D4）；单 worker 为既定部署形态，多 worker 列为 Non-Goal。
- [后台日志延迟或分页遗漏导致配对缺失] → 结束后延迟拉取 + 分页拉全 + 未配对显式标注，不凭空填值（D5）。
- [测试令牌泄露] → 全程加密存储、脱敏回显；令牌额度建议由管理员在 new-api 侧设为小额。
- [同平台并发运行污染日志归因] → 同平台串行约束（D4），牺牲并行换归因确定性。

## Migration Plan

1. `uv run main.py revision -m "add channel_eval tables"` 生成迁移（6 表）→ `uv run main.py upgrade --env=dev` 应用；生产同路径。
2. 菜单/权限种子 JSON 放入 `backend/sql/`，随启动 `initialize.py` 自动导入；已有环境重启即得"评估中心"菜单（默认超管）。
3. 回滚：`uv run main.py downgrade` 至前一版本（逆序 drop 6 表）+ 删除菜单种子记录；apiworld 侧已创建的 `eval-ch*` 标签与令牌无路由副作用，可保留或由管理员手动清理。

## Open Questions

- apiworld 的 new-api 实际版本与 `other.frt`/`request_id` 字段可用性——联调首日验证，降级路径已内建，不影响任务拆分。
- 导出 HTML 的最终版式细节（沿用纯 CSS 条形 vs 重构绘图）——导出契约（自包含、信息架构）已由 spec 固定，视觉细节实现期定。
