# Tasks: 图片生成工作台（image-workbench）

实现前阅读：仓库根 `SKILL.md`、`CONTEXT.md`、`docs/adr/0004`、`docs/adr/0005`、`proposal.md`、`design.md`、三份 spec。design.md 的两个 Open Question 均为联调期实测项（apiworld 的接入方式选择、其是否支持参考图），不改变以下拆分。

## 1. 后端：模块骨架与数据模型

- [ ] 1.1 创建 `app/modules/image_workbench/`（含 `plugin.toml`）及 `config/`、`job/` 两个功能子目录骨架（controller/service/crud/model/schema），并在 `app/api/v1/routers.py` 的 `DOMAIN_CONTROLLERS` 注册；验证：重启后端后 `/docs` 出现 `image_workbench` 标签（动态路由发现只在启动时执行，必须重启，见 SKILL.md）
- [ ] 1.2 按 design D2 实现三张表 model（`image_workbench_service` / `image_workbench_model` / `image_workbench_job`），`uv run main.py revision -m "add image workbench tables"` 生成迁移并 `uv run main.py upgrade --env=dev`；验证：迁移 upgrade/downgrade 可往返无报错，表字段与 D2 一致（含 `capability_json`/`params_json`/`usage_json` 与软删、`created_id` 列）
- [ ] 1.3 为三张表编写 model 层 pytest（默认值、外键关系、`created_id` 写入、软删标记）；验证：`cd backend && uv run pytest` 通过

## 2. 后端：绘图服务与绘图模型配置（service-config）

- [ ] 2.1 实现绘图服务 CRUD API（凭证 `CryptoUtil.encrypt` 落库、接口仅回显脱敏、编辑留空沿用原值、操作日志不含明文）；验证：pytest 覆盖创建 / 脱敏回显 / 留空沿用 / 日志脱敏四个断言
- [ ] 2.2 实现绘图模型 CRUD API（两级结构：按 `service_id` 过滤列表，能力声明按 design D3 的结构校验，模型标识原样存取不做规范化）；验证：pytest 覆盖能力声明的解析/序列化与模型标识原样返回
- [ ] 2.3 实现连通性测试 `POST /services/test-connection` 与 `POST /services/{id}/test-connection`（用表单值 / 已存值向上游发一次最小代价真实请求，成功返回可核对的模型标识，失败返回明确原因，且**不产生可见绘图任务**）；验证：pytest mock 上游覆盖成功与失败两分支，并断言任务表无新增行
- [ ] 2.4 实现引用保护与停用：绘图服务 / 绘图模型被绘图任务引用时拒绝删除并提示可停用；停用后不出现在工作台可选模型中；验证：pytest 覆盖"有引用拒绝删除"与"停用后 optionselect 不含该模型"两个断言

## 3. 后端：上游调用层

- [ ] 3.1 实现 `job/client.py` 的 `ark` 分支：`POST {base_url}/images/generations`，`Bearer` 鉴权，JSON 体含 `model/prompt/size/output_format/watermark/response_format`，参考图以 `image` 数组的 base64 数据形式置于**同一请求体**；验证：pytest mock HTTP 断言 URL 为 base_url + `/images/generations`（base_url 原样保留多级路径）、鉴权头、参考图字段形态
- [ ] 3.2 实现 `job/client.py` 的 `openai` 分支：文生图 `POST {base_url}/images/generations`（含 `n: 1`），有参考图时改走 `POST {base_url}/images/edits` 的 `multipart/form-data`；验证：pytest mock HTTP 断言两条路径的 URL、`Content-Type` 与表单字段
- [ ] 3.3 实现响应解析与两种返回兼容：`data[].url` 与 `data[].b64_json` 都能取到图片字节，`data[].error` 与上游错误码转为可读失败原因，`usage` 原样保留；验证：pytest 覆盖 url 响应、b64_json 响应、单图失败（`data[].error`）、上游 4xx/5xx 四种载荷

## 4. 后端：绘图任务执行引擎（generation）

- [ ] 4.1 实现任务提交 API（按模型能力声明校验尺寸 / 输出格式 / 参考图支持与张数上限；并发闸门：该用户存在 `pending`/`running` 任务即拒绝；事务提交后经 `after_commit` 启动执行）；验证：pytest 覆盖越界尺寸拒绝、超张数拒绝、不支持参考图的模型提交参考图被拒、并发闸门拒绝四个分支
- [ ] 4.2 实现 asyncio 执行引擎（`_BG_TASKS` 强引用集合、状态机 `pending→running→success/failed/cancelled`、await 边界检查取消）；验证：pytest 以假上游跑完一次任务并断言状态流转，另覆盖取消语义（取消后状态为 `cancelled`）
- [ ] 4.3 实现产物转存（拿字节 → 临时文件 → 默认存储节点 `adapter.upload` → `image_path` 落库 → 删临时文件；失败即任务 `failed`；**绝不把上游 URL 存进 `image_path`**）；验证：pytest 覆盖转存成功、上传失败、上游仅返回 b64 三条路径，并断言失败任务无 `image_path`
- [ ] 4.4 实现用量与耗时记录（`usage_json` 存上游用量 + 生成张数，`elapsed_ms` 记耗时）；验证：pytest 断言成功任务的 `usage_json` 含上游字段且 `elapsed_ms` 非空
- [ ] 4.5 实现进度推送与断线重连：`job/progress.py` 把进度写 Redis，`job/sse_manager.py` 子类化 `core.SSEConnectionManager`，`GET /jobs/{id}/events` 返回 `EventSourceResponse`；验证：pytest 覆盖进度写入；手工提交任务后详情页可观察进度推进
- [ ] 4.6 实现历史列表 / 详情 / 取消 / 重试 / 删除 / 下载 API：列表走既有数据范围过滤（默认仅本人）、越权读取被拒、重试以原参数新建任务且保留原失败记录、删除为软删 + `adapter.delete` 真删产物且进行中任务拒绝删除；验证：pytest 覆盖跨用户越权拒绝、重试产生新行、删除后产物文件不存在、进行中删除被拒四个断言
- [ ] 4.7 实现启动恢复钩子并在 `app/__init__.py` 的 lifespan 中接线（照 `channel_eval/run/engine.py:69` 的 `recover_interrupted_runs()` 先例，把遗留 `pending`/`running` 置为 `failed` 并标注执行进程中断）；验证：pytest 直接调用钩子断言状态修正与 `error` 文案

## 5. 后端：菜单、权限与种子（access）

- [ ] 5.1 在 `backend/sql/sys_menu.json` 新增"绘图工作台"一级菜单及其两个子菜单（`工作台` → `image_workbench/job/index` / `ImageWorkbenchJob`、`绘图服务` → `image_workbench/config/index` / `ImageWorkbenchConfig`，均 `keep_alive: true`）与 design D9 的全部按钮权限码；验证：重启后超管可见菜单且两个页面可进入，普通角色不可见且接口 403
- [ ] 5.2 新增 `backend/sql/image_workbench_service.json` 与 `image_workbench_model.json`（火山方舟 Agent Plan 服务，凭证为空；pro / lite 两个模型的能力声明，尺寸分别为 `1K/1.5K/2K` 与 `2K/3K/4K`）；在 `initialize.py` 的 `prepare_init_models` 按依赖顺序加入两张表，并照 `__create_dict_data_objs` 先例新增按服务名解析 `service_id` 的 FK 解析方法；验证：全新库初始化后种子落库且 `api_key_enc` 为空，`uv run pytest` 通过
- [ ] 5.3 在 `initialize.py` 现有 `__ensure_seed_menu_merged(db, route_name="ChannelEval", ...)` 之后增加 `route_name="ImageWorkbench", title="绘图工作台"` 的调用；验证：对已有数据的库连续重启两次，菜单子树始终只有一份，无重复项

## 6. 前端：API 层与绘图服务配置页

- [ ] 6.1 新建 `api/module_image_workbench/{service,model,job}.ts`，与 design D9 的契约逐条对齐（路径、方法、类型）；验证：`cd frontend/web && pnpm ts:check` 通过
- [ ] 6.2 实现 `views/image_workbench/config/index.vue`（绘图服务列表 + 其下绘图模型的编辑，复用 `useTable` / `FaTable` / `FaSearchBar` / `useCrudDialog` / `useCrudForm` / `FaDialog`，不手写 ElTable/ElDialog），`defineOptions({ name: 'ImageWorkbenchConfig' })` 与 `route_name` 一致；验证：`pnpm ts:check && pnpm lint && pnpm test` 通过；页面可完成"新建服务 → 填凭证 → 添加模型 → 测试连接"全流程
- [ ] 6.3 实现配置页的能力声明表单（design D3 全部字段，含尺寸取值的可增删列表与默认输出格式），并在 base_url 输入框给出完整前缀的填写提示（区别于渠道评估「平台」的 base_url 语义）；验证：保存后重新打开，能力声明原样回显；base_url 留空或语义错误时有明确提示
- [ ] 6.4 补充 i18n 词条到 `locales/langs/zh.json` 与 `en.json` 两份；验证：`pnpm lint` 通过，切换英文后配置页无未翻译文案

## 7. 前端：绘图工作台页

- [ ] 7.1 实现 `views/image_workbench/job/index.vue` 的生成表单：模型选择器（只列启用且服务已配凭证的模型）、按所选模型能力声明联动尺寸选项与参考图入口、参考图经 `POST /common/file/upload` 上传、参数越界时前端先拦；`defineOptions({ name: 'ImageWorkbenchJob' })` 与 `route_name` 一致；验证：`pnpm ts:check && pnpm lint && pnpm test` 通过；切换到两个尺寸档位不同的模型时尺寸选项随之变化
- [ ] 7.2 实现历史列表、详情与状态展示（提示词、模型、参数、状态、耗时、产物缩略），接入 SSE 订阅与 Redis 进度兜底（断线重连后重新取一次当前状态，不停留在"进行中"）；**必须实现 `onActivated` / `onDeactivated`**：`deactivated` 时断流，`activated` 时按需重连（`onUnmounted` 不是唯一清理点）；验证：提交任务后切换菜单再切回，连接不泄漏、状态仍正确
- [ ] 7.3 实现取消（确认框明确写"不保证不产生上游费用"）、失败任务一键重试、下载产物、删除（确认框明确写产物不可恢复）；按 design D9 的权限码加 `v-hasPerm`；验证：无 `image_workbench:job:delete` 权限的账号看不到删除按钮，且绕过前端直调删除接口被后端拒绝
- [ ] 7.4 补充工作台页 i18n 词条到 `locales/langs/zh.json` 与 `en.json` 两份；验证：`pnpm lint` 通过，英文模式下无未翻译文案

## 8. 端到端验收

- [ ] 8.1 真实上游联调：配置火山方舟 Agent Plan 服务并填专属 Base URL 与专属 API Key，验证文生图成功出图、参考图生成成功、产物在上游 URL 失效后仍可访问；**记录实测结论**：apiworld 选哪个接入方式、apiworld 是否支持参考图（design.md 的两个 Open Question）
- [ ] 8.2 失败与恢复路径验收：构造上游错误验证失败记录与一键重试；执行中重启后端验证任务被置为 `failed` 而非悬空；验证取消后并发额度被释放
- [ ] 8.3 权限验收：以未授权账号验证菜单不出现、接口 403；以仅持有查询权限的账号验证生成/删除按钮不出现；验证数据范围——甲看不到乙的绘图任务且下载被拒
- [ ] 8.4 全量命令验收：`cd backend && uv run pytest`；`cd frontend/web && pnpm ts:check && pnpm lint && pnpm test`；按 `SKILL.md` 第 9 节重建并同步三份 dist 产物（`pnpm build:prod` + 带 `--delete` 的 rsync 到 `docker/nginx/web/dist` 与 `backend/dist`）