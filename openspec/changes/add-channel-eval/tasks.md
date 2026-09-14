# Tasks: 渠道评估能力（channel-eval）

实现前阅读：仓库根 `SKILL.md`、`CONTEXT.md`、`docs/adr/0001~0003`、`proposal.md`、`design.md`、三份 spec。Open Questions 均为联调期验证项，不改变以下拆分。

## 1. 后端：模块骨架与数据模型

- [ ] 1.1 创建 `app/modules/channel_eval/`（含 plugin.toml）及 `platform/`、`run/`、`report/` 三个功能子目录骨架（controller/service/crud/model/schema），并在 `app/api/v1/routers.py` 注册；验证：重启后端后 `/docs` 出现 `channel_eval` 标签（动态路由发现需重启，见 SKILL.md）
- [ ] 1.2 按 design D1 实现 6 张表 model（platform/provision/run/combo/sample/report），`uv run main.py revision -m "add channel_eval tables"` 生成迁移并 `upgrade --env=dev`；验证：迁移可重复 upgrade/downgrade 无报错，表结构与 D1 字段一致
- [ ] 1.3 为 run/report 编写 model 层 pytest（建表、默认值、外键级联删除语义）；验证：`cd backend && uv run pytest` 通过

## 2. 后端：平台管理（platform-access）

- [ ] 2.1 实现平台 CRUD API（凭证 CryptoUtil 加密、接口仅回显脱敏；删除前校验存在运行则拒绝）；验证：pytest 覆盖创建/脱敏回显/删除保护，`uv run pytest` 通过
- [ ] 2.2 实现 `POST /platforms/{id}/test-connection`（用所填凭证发只读请求，返回成功/失败原因）；验证：pytest mock 外部响应覆盖成功与失败分支
- [ ] 2.3 实现 `GET /platforms/{id}/channels`（实时拉取 new-api 渠道目录：编号/名称/状态/分组/优先级/权重/模型列表；不持久化；失败返回明确错误）；验证：pytest mock 验证字段映射与不缓存行为
- [ ] 2.4 实现路由可达性提示计算（同分组同模型优先级分层时标记备用渠道，仅提示不阻断）；验证：pytest 覆盖分层与平权两种目录
- [ ] 2.5 新增"评估中心"菜单种子（一级菜单 + 平台管理/测试运行/对比报告子菜单 + 按钮权限 `channel_eval:*:*`，默认超管）；验证：重启后超管可见菜单，普通角色不可见且接口 403

## 3. 后端：new-api 集成层与分组定向

- [ ] 3.1 实现 `NewApiClient`：`list_channels/list_tokens/list_logs/list_self_logs` 只读方法 + 仅有两个写方法 `append_channel_group/create_eval_token`；验证：pytest mock HTTP 层，代码评审确认无其它写路径
- [ ] 3.2 实现 provision 服务（`eval-ch{渠道ID}` 约定：先查后建、幂等、令牌明文创建后即加密入 `channel_eval_provision`）；验证：pytest 模拟重复执行，断言不重复建标签/令牌
- [ ] 3.3 日志字段解析与降级（`use_time`/`other.frt`/`request_id` 存在性探测，缺失时对应字段置空）；验证：pytest 覆盖全字段与缺字段两种日志载荷

## 4. 后端：测试运行执行引擎（test-run）

- [ ] 4.1 实现运行创建 API（逐渠道「渠道 × 模型」组合校验、参数快照入 params_json、同平台串行约束：存在 pending/running 则拒绝）；验证：pytest 覆盖空组合拒绝与串行冲突拒绝
- [ ] 4.2 实现 asyncio 执行引擎（状态机 pending→running→success/failed/cancelled、进程内任务注册表、组合边界取消检查、同组合顺序请求+间隔）；验证：pytest 以假 LLM 流式响应跑完一次运行并断言状态流转与取消语义
- [ ] 4.3 实现客户端实测采集（流式 chat 请求：TTFT=首内容 delta、总耗时=至 [DONE]、tps、request_id 响应头、失败样本记错误；热身 is_warmup 不计聚合）；验证：pytest mock SSE 流断言各指标口径
- [ ] 4.4 实现分组定向执行接线（按组合取 provision 令牌发请求；准备失败该组合标记失败并继续）；验证：pytest 覆盖定向成功与准备失败分支
- [ ] 4.5 实现进度推送与取消 API（进度写 Redis、SSE `GET /runs/{id}/events` 推送、cancel 置 cancelling）；验证：pytest 覆盖进度写入；手工运行详情页可观察进度推进
- [ ] 4.6 实现启动恢复钩子（进程启动时将 running/cancelling 置 failed，error=进程重启）；验证：pytest 模拟启动钩子断言状态修正

## 5. 后端：后台日志配对与聚合

- [ ] 5.1 实现运行结束后日志拉取（延迟约 5s、按 token_name+时间窗分页拉全；基准渠道走 list_self_logs）；验证：pytest mock 分页日志断言拉全逻辑
- [ ] 5.2 实现 request_id 1:1 配对与"后台无记录"标注（be_* 字段写回 sample）；验证：pytest 覆盖配对成功、缺失、重复 request_id 三种情况
- [ ] 5.3 实现组合聚合（剔除热身/异常/失败样本的均值 + 含异常参考均值 + 成功率，写回 combo 计数）；验证：pytest 按 spec 异常口径场景断言（如 45s 样本在 30s 阈值下被剔除）

## 6. 后端：对比报告（compare-report）

- [ ] 6.1 实现报告 CRUD + 基准校验（基准运行须为基准渠道的成功运行；run_ids/baseline_run_id/note 持久化）；验证：pytest 覆盖基准校验拒绝分支
- [ ] 6.2 实现 `GET /reports/{id}/view` 聚合（按模型分组并列各运行组合与基准；客户端 vs 后台交叉对比与差值；无数据"—"、无基准"无基准数据"）；验证：pytest 构造多运行数据断言聚合结构
- [ ] 6.3 实现自动结论规则（仅阈值事实：高于基准幅度、异常样本、成功率非 100%；无评价性措辞）；验证：pytest 断言结论文案只含事实陈述
- [ ] 6.4 实现 `GET /reports/{id}/export`（Jinja2 自包含 HTML：报告头/KPI/图表/明细/交叉对比/异常清单/结论与备注/口径说明，数据内嵌零外部依赖）；验证：pytest 断言导出物为单文件且无外部资源引用；浏览器离线打开人工核对

## 7. 前端：API 层与平台管理页

- [ ] 7.1 新建 `api/module_channel_eval/{platform,run,report}.ts`（与后端契约对齐）；验证：`cd frontend/web && pnpm ts:check` 通过
- [ ] 7.2 平台管理页（列表/表单/测试连接/基准标识，复用 useTable+FaTable+useCrudDialog；凭证脱敏展示）；验证：`pnpm ts:check && pnpm lint` 通过，页面手工走查增删改查
- [ ] 7.3 i18n 词条 zh/en 双份（平台管理与公共词条）；验证：切换语言无遗漏文案

## 8. 前端：测试运行页

- [ ] 8.1 运行创建向导（选平台→实时拉渠道树→逐渠道勾模型→参数表单：次数/热身/间隔/超时/prompt/异常阈值；备用渠道提示展示）；验证：向导全流程可提交创建，`pnpm ts:check` 通过
- [ ] 8.2 运行列表与详情（状态流转展示、SSE 进度、组合聚合表、样本明细表含客户端/后台两侧数据与"后台无记录"标注、取消按钮）；验证：SSE 断线重连资源释放在 onActivated/onDeactivated 实现，`pnpm ts:check && pnpm lint` 通过

## 9. 前端：对比报告页

- [ ] 9.1 报告列表与编辑器（选择多次运行 + 基准运行[默认基准渠道最新成功、可改选] + 备注）；验证：`pnpm ts:check` 通过，基准校验错误前端可见
- [ ] 9.2 报告视图（按模型分组对比图[复用 fa-dual-bar-compare-chart]与明细表、交叉对比、异常清单、自动结论与备注区、导出 HTML 按钮）；验证：页面渲染与 view 接口数据一致，导出文件可下载且离线打开完整

## 10. 联调与验收

- [ ] 10.1 用 apiworld 真实环境联调：核实 `other.frt`/`X-Oneapi-Request-Id` 字段可用性（Open Question 1），缺失则走降级路径并记录；验证：一次真实运行的样本全部成功配对或明确标注
- [ ] 10.2 端到端验收：apiworld 选 2~3 渠道 × 2~3 模型发起运行，与基准渠道运行组成报告并导出 HTML，对照 spec 三份文件逐场景核对；验证：导出报告数据与运行详情一致，写操作白名单未被触碰（apiworld 渠道优先级/权重/启停前后一致）
- [ ] 10.3 全量回归：`cd backend && uv run pytest`、`cd frontend/web && pnpm ts:check && pnpm lint && pnpm test`；验证：全部通过
