# Proposal: 渠道评估能力（channel-eval）

## Why

当前评估 apiworld 上游渠道（京东云 / UCloud / Runapi 等）性能依赖手工流程：本地脚本发请求、人工导出平台后台日志、手工对齐时间戳、手写 HTML 报告（`model_compare.html`），出一份报告约半天且无法复现、无法追溯。需要在 FastAPIAdmin Web 端提供一键发起、数据落库、可随时重跑与对比的渠道评估能力，并引入可维护的基准渠道数据作为横向参照。

## What Changes

- 新增**平台管理**：登记评估目标平台（apiworld 等 new-api 站点）与基准渠道（aihubmix），凭证加密存储；渠道与模型目录实时从 new-api 拉取，评估系统不重复配置。
- 新增**指定渠道自动准备**：评估系统在每次运行创建时经 new-api 管理接口创建 1 个专用管理员令牌（命名约定 `eval-run{运行ID}`，与日常测试令牌隔离），并用其请求头 `Bearer <key>-<渠道ID>` 把每个请求 pin 到待测渠道（new-api v1.0.0-rc.21+ 原生机制），使测试请求 100% 命中指定渠道；对 apiworld 的写操作仅限创建该专用令牌一项，永不触碰优先级/权重/启停/模型映射。
- 新增**测试运行**：管理员按「先选渠道、再选该渠道下的模型」组织「渠道 × 模型」组合，后端异步任务逐组合发起真实流式请求（默认每组合 3 次 + 可选热身），记录 TTFT/总耗时/tokens/成败；运行结束后自动拉取平台后台日志并与样本按请求标识 1:1 配对；运行状态机 + SSE 进度推送；数据永久保留。
- 新增**对比报告**：选取一至多次运行 + 一次基准运行生成 Web 对比视图与可导出自包含 HTML 报告；报告生成时冻结所引用数据快照（含基准）；含客户端实测 vs 后台日志交叉对比、异常清单、规则自动结论 + 管理员备注。
- 新增"评估中心"一级菜单（平台管理 / 测试运行 / 对比报告），标准 RBAC，默认仅超级管理员可见。

## Capabilities

### New Capabilities

- `channel-eval/platform-access`: 平台接入与渠道目录——平台登记与凭证管理、基准渠道标记、渠道/模型目录实时拉取与可达性提示、指定渠道（专用评估令牌 + pin）自动准备、对外部系统写操作的白名单约束。
- `channel-eval/test-run`: 测试运行与数据采集——运行创建（逐渠道挑选「渠道 × 模型」组合与参数快照）、异步执行与进度、客户端实测指标采集、热身与异常口径、平台后台日志拉取与样本 1:1 配对、运行与样本的持久化。
- `channel-eval/compare-report`: 对比报告——多运行与基准运行的对比视图、基准快照冻结、客户端实测与后台日志交叉对比、异常与含异常均值的展示口径、自动结论与管理员备注、自包含 HTML 导出。

### Modified Capabilities

（无——项目尚无既有 specs，全部为新增 capability。）

## Impact

- **backend**：新增 `app/modules/channel_eval/`（platform / run / report 等功能，含 controller/service/crud/model/schema 与 plugin.toml）；`app/api/v1/routers.py` 注册路由；新增数据表（平台、运行、样本、后台日志快照等）需 Alembic 迁移；`backend/sql/*.json` 增加菜单种子数据；复用 `core/ap_scheduler`（仅预留）、`core/sse_manager`（进度推送）、`CryptoUtil`（凭证加密）、httpx（外部调用）。
- **frontend-web**：新增 `api/module_channel_eval/`、`views/channel_eval/`（平台管理、测试运行、对比报告三类页面与私有组件）；i18n 词条 zh/en 双份；菜单经种子数据下发，页面组件名与 route_name 对齐。
- **frontend-app / docker / docs 站**：无影响。
- **外部系统**：apiworld（new-api）——只读拉取渠道目录与日志；受控写仅限「创建专用评估令牌」一个接口。基准渠道 aihubmix 仅用户级只读接口。
- **决策记录**：`CONTEXT.md`、`docs/adr/0001`（指定渠道）、`docs/adr/0002`（基准快照）、`docs/adr/0003`（目录拉取免配置）已在设计阶段落定，实现须遵循。
