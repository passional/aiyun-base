## Context

动机见 `proposal.md`——Why。此处只列影响方案的现存事实与约束：

- **模块落法与既有一致**：`app/modules/<模块>/<功能>/` 固定五件套 + 模块根 `plugin.toml`；路由在 `app/api/v1/routers.py` 的 `DOMAIN_CONTROLLERS` 注册；权限用 `Security(AuthPermission([...]))`；动态发现只在启动时执行，改完必须重启。
- **异步执行有先例可抄**：`channel_eval/run/engine.py` 用模块级强引用集合 `_BG_TASKS` 持有裸 `asyncio.Task`（FastAPI 不持有引用会被静默取消），状态机 `pending→running→success/failed/cancelled`，`cancelling` 作取消过渡态在边界检查退出；`recover_interrupted_runs()` 在 `app/__init__.py` 的 lifespan 中被调用，把进程重启前遗留的执行中状态置为 failed。
- **SSE 基建已就绪**：`core/sse_manager.py` 提供进程内连接表 + Redis pub/sub 跨进程广播，`SSE_QUEUE_MAX_SIZE=64` 打满即踢除慢消费者；`channel_eval` 有可抄的整套做法——`run/sse_manager.py` 子类化 + `run/progress.py` 把进度写 Redis（断线重连以 Redis 为准）+ `from fastapi.sse import EventSourceResponse`。
- **存储抽象已就绪**：`task/storage/core/factory.py` 的 `StorageAdapterFactory.create(StorageAdapterConfig)` 支持 ftp/sftp/s3/obs/oss/cos/local；种子节点 `task_storage_node` 已有一条 `is_default: true` 的「本地上传目录」（`host: static/upload`）。docker-compose 已把 `./backend/static/upload` bind-mount 到宿主机，单 backend 容器。
- **凭证加密已就绪**：`app/utils/crypto_util.py` 的 `CryptoUtil.encrypt/decrypt`（Fernet + 密钥环轮换），`channel_eval/platform/service.py` 有「加密落库 + 脱敏回显 + 编辑留空沿用」的完整先例。
- **权限体系**：菜单即权限（`sys_menu` 同时是权限目录与路由注册表），权限码三段式；`sys_role_menus` 从不种子化；存量库新菜单靠 `initialize.py` 里硬编码的 `__ensure_seed_menu_merged()` 补齐（当前只登记了 `ChannelEval` 一条）。
- **部署与网络**：目标是内网服务器（`DEPLOYMENT.md`），出网可用（`channel_eval` 已在调 apiworld）；但后端自身的文件地址是内网地址，公网不可达。
- **上游约束**：火山方舟 Agent Plan 必须用专属 Base URL `https://ark.cn-beijing.volces.com/api/plan/v3` 与专属 API Key，混用会调用失败或产生额外费用；图片返回的 `url` **24 小时后失效**。

## Goals / Non-Goals

**Goals:**

- 管理员可在后台自配绘图服务与绘图模型，模型能力（尺寸档位、参考图、输出格式、水印默认值）逐模型可配而非代码硬编码。
- 登录用户可做单次生成并保留历史，产物在远超过上游 URL 有效期后仍可访问。
- 整块功能经既有菜单驱动 RBAC 授权，前后端权限码字面量一致。
- 复用既有基建（SSE、存储适配层、凭证加密、异步任务模式），不引入新的中间件或部署单元。

**Non-Goals:**

- 不做组图、图层拆分、交互编辑、联网搜索、流式输出。其中**组图、联网搜索、流式输出是 Seedream 5.0 pro 本就不支持的上游能力**；而**图层拆分（`layer_decomposition`，产出一张底图 + 最多 16 个分层）与交互编辑（在提示词中写入归一化坐标标记）恰是 5.0 pro 的两项旗舰能力**，本变更仅因范围控制未纳入实现，留待后续变更。（原文误将后两项与"pro 不支持"并列，2026-09-17 订正。）
- 不做批量生成、不做独立 worker 进程或任务队列。
- 不做额度拦截与计费（只记录用量，见 `specs/image-workbench/generation`）。
- 不做小程序端。
- 不做渠道 pin，不复用渠道评估的「平台」凭证（见 `docs/adr/0004`）。
- 不为非本地存储协议实现产物签名 URL——首期只用默认本地节点。

## Decisions

### D1 模块落法：两个功能目录，不设独立的上游客户端包

```
app/modules/image_workbench/
├── plugin.toml
├── config/          # 绘图服务 + 绘图模型（管理员配置）
│   ├── controller.py service.py crud.py model.py schema.py
└── job/             # 绘图任务
    ├── controller.py service.py crud.py model.py schema.py
    ├── engine.py    # 异步执行 + 产物转存
    ├── client.py    # 上游调用（按接入方式分支）
    ├── progress.py  # Redis 进度（断线重连以它为准）
    └── sse_manager.py
```

功能目录按**能力**划分而非按实体：`config` 一个目录同时容纳绘图服务与绘图模型两张表的 CRUD（它们是主从关系，永远一起被编辑），`job` 独占绘图任务。这样避免了 `model/model.py`、`service/service.py` 这类同名歧义——仓库里 `model.py` 是 ORM 层的固定文件名，再拿 `model` 当目录名会读起来含混。

上游调用放在 `job/client.py` 而非独立包，与 `channel_eval` 把 `new_api_client.py` 放在 `run/` 下同理：只有 `job` 会调用上游。

**备选**：按实体拆三个功能目录（`service/`、`model/`、`job/`）——放弃，理由是 `model/` 与 `service/` 会与同层的 `model.py`、`service.py` 撞名，且绘图服务与绘图模型从无独立编辑的场景。

### D2 数据模型：三张表

`image_workbench_service`（绘图服务）

| 列 | 说明 |
| --- | --- |
| `name` | 名称，唯一 |
| `dialect` | 接入方式，`ark` / `openai` |
| `base_url` | 完整 API 前缀（含 `/v1` 或 `/api/plan/v3`） |
| `api_key_enc` | 调用凭证密文（Fernet） |
| `status` | 启用状态 |
| `remark` | 备注 |

`image_workbench_model`（绘图模型）

| 列 | 说明 |
| --- | --- |
| `service_id` | FK → `image_workbench_service.id`，`ondelete="CASCADE"` |
| `model_id` | 上游真实模型标识，原样提交 |
| `display_name` | 工作台展示名 |
| `capability_json` | 能力声明（见 D3） |
| `status` | 启用状态 |
| `remark` | 备注 |

`image_workbench_job`（绘图任务）

| 列 | 说明 |
| --- | --- |
| `service_id` / `model_pk` | FK（`RESTRICT` 语义由服务层保证，见 D8） |
| `model_id` | 模型标识快照（防上游标识漂移影响历史展示） |
| `active_owner_id` | 并发闸门占位：进行中等于创建者 ID，终态为 NULL，唯一索引保证每用户最多一个进行中任务 |
| `prompt` | 提示词 |
| `params_json` | 本次参数快照：`size` / `output_format` / `watermark` / `reference_images`（本系统内相对路径数组） |
| `status` | `pending` / `running` / `success` / `failed` / `cancelled` |
| `error` | 失败原因 |
| `usage_json` | 用量明细（上游 `usage` 原样 + 生成张数） |
| `elapsed_ms` | 任务耗时 |
| `image_path` | 产物在本系统存储节点中的相对路径（成功时非空） |
| `image_size` | 产物宽高，如 `2048x2048` |
| `started_at` / `finished_at` | 起止时间 |

三张表均继承 `ModelMixin`（含 `is_deleted` 软删与时间戳）；`job` 另继承 `UserMixin`，`created_id` 即创建者——数据范围过滤依赖它，无需自建 `user_id`。

**参考图复用已有文件**：用户经模块专属 `POST /image_workbench/jobs/reference-images/upload` 上传的参考图已经落在 `static/upload` 下，`params_json.reference_images` 只存这些相对路径，执行时按接入方式读取并编码上行，**不重复拷贝**。专属入口要求生成权限并按真实文件内容校验图片格式，不改变公共文件接口的权限。

### D3 能力声明的结构

```jsonc
{
  "sizes": ["1K", "1.5K", "2K"],      // 可选尺寸取值（档位字符串或 "宽x高"），空数组表示该模型无尺寸概念
  "allow_custom_size": false,          // 是否额外允许管理员/用户填自定义 "宽x高"
  "support_reference": true,           // 是否支持参考图
  "max_reference_images": 10,          // 参考图张数上限
  "output_formats": ["png", "jpeg"],   // 可选输出格式
  "default_output_format": "png",
  "watermark_default": false,          // 本次是否加水印的默认值
  "extra_params": {}                   // 原样透传给上游的默认参数
}
```

`watermark_default` 默认 **false**（上游默认是 true，不显式传 false 会给成品图打上「AI 生成」水印）；后台表单可改，用户侧不暴露该开关。

尺寸取值**不做归一化翻译**：上游 A 收档位字符串、上游 B 收固定像素枚举，硬套一层"方/横/竖"的映射会在像素与宽高比约束（如 Seedream 5.0 pro 的总像素 `[921600, 4624220]`、宽高比 `[1/16, 16]`）上立刻碎掉，也会把上游错误挡在本系统里、让排查变难。

**备选**：能力声明内置代码表按模型标识前缀匹配——放弃，因为同一模型在不同入口下写法不同（`doubao-seedream-5-0-pro` vs `doubao-seedream-5-0-pro-260628`），前缀匹配必然漏。

### D4 上游调用：接入方式分支，响应两种都吃

`job/client.py` 按 `dialect` 分支：

- `ark`：`POST {base_url}/images/generations`，`Authorization: Bearer <key>`，JSON 体 `{model, prompt, size, output_format, watermark, response_format, extra_params...}`；有参考图时加 `image` 字段（数组，`data:<mime>;base64,...` 形式）。文生图与图生图**同一个端点**。
- `openai`：文生图 `POST {base_url}/images/generations`，JSON 体 `{model, prompt, size, n: 1, response_format}`；有参考图时 `POST {base_url}/images/edits`，`multipart/form-data`（`image` 文件字段 + `prompt` + `model` + `size`）。

请求 `response_format: "url"`（体积小、兼容面最广），拿到响应后**同时兼容** `data[].url` 与 `data[].b64_json` 两种返回：有 `b64_json` 直接落盘，否则立即下载 `url`。两条路都在 D5 里被同一段转存逻辑收口。

### D5 异步执行与产物转存：抄 `channel_eval/run` 的整套

- 提交时落库 `status=pending`，**事务提交后**经 `after_commit` 启动后台执行（提前启动会读不到未提交行，这是 `channel_eval/run/service.py:89` 记下的坑）。
- `asyncio.create_task` + 模块级 `_BG_TASKS` 强引用集合防 GC。
- 状态机 `pending→running→success/failed/cancelled`；取消用 `cancelling` 过渡态或直接取消 task，在 await 边界检查退出。
- 进度写 Redis（`job/progress.py`）供断线重连，与 SSE 推送并行——推送丢了也不会卡在"进行中"。
- **产物转存**：拿到图片字节后写入临时文件 → 经默认存储节点 `adapter.upload(tmp, remote)` → `image_path` 落库 → 删临时文件。任一步失败即任务 `failed`。**绝不在 `image_path` 里存上游 URL。**
- 产物远端路径：`image_workbench/{yyyy}/{mm}/{job_uuid}.{ext}`（按日期分片，与 `UploadUtil` 的既有做法一致）。
- `recover_interrupted_runs()` 的对应物接进 `app/__init__.py` 的 lifespan，把遗留 `pending`/`running` 置为 `failed`（`error` 标注执行进程中断）。

### D6 并发闸门与取消

- 提交前统计该用户 `status in (pending, running)` 的任务数，`>= 1` 即拒绝（`CustomException`，明确提示）；任务表另以唯一 `active_owner_id` 作为原子兜底，避免两个并发请求同时通过计数检查。
- `pending` / `running` 时 `active_owner_id=created_id`，进入任一终态时清空为 NULL；数据库唯一索引保证同一用户最多占用一个活跃槽位。
- 取消：置 `cancelling`，引擎在 await 边界退出并把状态落为 `cancelled`；已终态的任务取消被拒绝。
- 取消语义在接口返回与前端确认框里都写明「不保证不产生上游费用」——我们不掌握上游的计费时点。

### D7 存储与产物访问

产物经 `StorageAdapterFactory` 落到默认节点。默认节点协议为 `local`（`host: static/upload`），因此产物 URL = `{STATIC_URL}/{remote_path}`，前端可直接 `<img>`。

**局限（已知并接受）**：非本地协议的产物需要签名 URL，首期不实现；将来把默认节点换成 S3/OSS/COS 时，需要补一段"取可访问 URL"的适配——这是加一层，不是重构。

### D8 删除与引用保护

- 绘图服务 / 绘图模型被绘图任务引用时**拒绝物理删除**（软删也被拒，避免历史任务的模型信息悬空），提示可改为停用。
- 工作台可选模型 = 模型启用 **且** 所属服务启用 **且** 服务已配置凭证。种子预置的火山方舟服务凭证为空，因此预置完成后不会在工作台意外可选。
- 删除绘图任务 = 软删记录 + 调 `adapter.delete(image_path)` **真删产物**；进行中的任务拒绝删除。

### D9 API 契约

统一前缀 `/api/v1/image_workbench`，`OperationLogRoute`，`Security(AuthPermission([...]))`。

| 方法 | 路径 | 权限码 | 说明 |
| --- | --- | --- | --- |
| GET | `/services/list` | `image_workbench:service:query` | 绘图服务分页列表 |
| GET | `/services/optionselect` | `image_workbench:service:query` | 下拉选项 |
| GET | `/services/detail/{id}` | `image_workbench:service:query` | 详情（凭证脱敏） |
| POST | `/services/create` | `image_workbench:service:add` | 创建 |
| PUT | `/services/update/{id}` | `image_workbench:service:edit` | 编辑（凭证留空沿用） |
| DELETE | `/services/delete` | `image_workbench:service:delete` | 删除（引用保护） |
| POST | `/services/test-connection` | `image_workbench:service:edit` | 用表单值测连通性（未保存） |
| POST | `/services/{id}/test-connection` | `image_workbench:service:edit` | 用已存值测连通性 |
| GET | `/models/list` | `image_workbench:service:query` | 绘图模型列表（按 `service_id` 过滤） |
| POST | `/models/create` | `image_workbench:service:add` | 创建 |
| PUT | `/models/update/{id}` | `image_workbench:service:edit` | 编辑 |
| DELETE | `/models/delete` | `image_workbench:service:delete` | 删除（引用保护） |
| GET | `/jobs/models/optionselect` | `image_workbench:job:query` | 工作台可选模型（含能力声明） |
| POST | `/jobs/reference-images/upload` | `image_workbench:job:create` | 上传并校验参考图 |
| POST | `/jobs/create` | `image_workbench:job:create` | 提交生成（并发闸门 + 参数校验） |
| GET | `/jobs/list` | `image_workbench:job:query` | 历史列表（数据范围过滤） |
| GET | `/jobs/detail/{id}` | `image_workbench:job:query` | 详情 |
| POST | `/jobs/{id}/cancel` | `image_workbench:job:create` | 取消 |
| POST | `/jobs/{id}/retry` | `image_workbench:job:create` | 以原参数新建任务 |
| DELETE | `/jobs/delete` | `image_workbench:job:delete` | 删除（软删 + 删产物） |
| GET | `/jobs/{id}/download` | `image_workbench:job:download` | 下载产物 |
| GET | `/jobs/{id}/events` | 登录态（SSE） | 任务进度实时流，`EventSourceResponse` |

响应统一 `SuccessResponse(data=..., msg=...)` + `response_model=ResponseSchema[T]`。

### D10 菜单与种子

`backend/sql/sys_menu.json` 新增一级菜单 `绘图工作台`（`route_path: /image-workbench`，`route_name: ImageWorkbench`），下挂两个子菜单：

- `工作台`：`route_path: job`、`route_name: ImageWorkbenchJob`、`component_path: image_workbench/job/index`、`keep_alive: true`
- `绘图服务`：`route_path: config`、`route_name: ImageWorkbenchConfig`、`component_path: image_workbench/config/index`、`keep_alive: true`

按钮子项按 D9 的权限码逐个建（`type: 3`）。两个页面组件的 `defineOptions({ name })` MUST 与 `route_name` 一致，否则 KeepAlive 与工作栏按组件名匹配的机制会失灵。

`backend/sql/image_workbench_service.json` 与 `image_workbench_model.json` 预置火山方舟 Agent Plan 服务（凭证为空）+ pro / lite 两个模型的能力声明。`initialize.py` 的 `prepare_init_models` 需按依赖顺序加入两张表，并**照 `__create_dict_data_objs` 的先例新增一个 FK 解析方法**（绘图模型种子靠服务名解析 `service_id`）——现有 `__create_objects_with_children` 只处理 `sys_menu`/`sys_dept` 的父子关系，不处理任意外键。

存量库补种：在 `initialize.py` 现有 `__ensure_seed_menu_merged(db, route_name="ChannelEval", ...)` 之后，增加一条 `route_name="ImageWorkbench", title="绘图工作台"`。种子表是**新表**、初始为空，因此常规种子导入对存量库同样生效（`__init_data` 只跳过"已有数据"的表），不需要额外的补种逻辑。

### D11 前端

- `api/module_image_workbench/{service.ts, model.ts, job.ts}`，对象字面量方法 + `request`。
- `views/image_workbench/config/index.vue`：绘图服务列表 + 其下绘图模型的编辑（复用 `useTable` + `FaTable` + `FaSearchBar` + `useCrudDialog`/`useCrudForm` + `FaDialog`）。
- `views/image_workbench/job/index.vue`：工作台——左侧生成表单（模型选择器按能力声明联动尺寸/参考图/格式），右侧历史列表与详情。
- SSE 订阅走 `utils/sse`；页面必须实现 `onActivated` / `onDeactivated`：`deactivated` 时断流释放连接，`activated` 时按需重连并拉一次当前状态。
- i18n 词条同步加 `locales/langs/zh.json` 与 `en.json` 两份。
- 跨端影响：**仅 frontend/web**，`frontend/app` 与 `frontend/docs` 不动。

## Risks / Trade-offs

- **[上游 URL 24 小时失效，转存失败即产物永久丢失]** → 取得结果后立即转存；转存失败把任务判为 `failed` 而非留下失效链接；用户可一键重试。
- **[模型标识与能力档位随上游改版漂移]** → 能力声明存库可配、模型标识手填；种子值只作占位提示，不做代码内置匹配表。
- **[长任务 + 进程重启导致任务悬空]** → lifespan 里的恢复钩子把 `pending`/`running` 置为 `failed`，用户可重试；不伪装成成功。
- **[Seedream 参考图需 base64 上行，大图会撑大请求体]** → 沿用 `upload_util` 的既有上传体积限制；参考图上限由能力声明约束并在后端强制校验。
- **[本地存储节点在多实例部署下坏掉]** → 首期部署是单 backend 容器且已 bind-mount；换对象存储节点即可扩展，但需补签名 URL 一段（见 D7）。
- **[取消可能仍产生上游费用]** → 不试图隐藏，接口与前端确认框都明确告知；这是产品语义而非缺陷。
- **[exhausted 的 SSE 慢消费者]** → 复用 `SSE_QUEUE_MAX_SIZE` 的"队列打满即踢除"策略，不引入无界堆积。
- **[apiworld 的 OpenAI 兼容层是否支持内联 `image` 字段尚不确定]** → 代码支持两种接入方式，管理员按实测结果选择；不影响规格与任务拆分。

## Migration Plan

1. 后端改动落到 `app/modules/image_workbench/`，在 `app/api/v1/routers.py` 的 `DOMAIN_CONTROLLERS` 注册。
2. 生成并应用迁移：`uv run main.py revision -m "add image workbench tables"` → `uv run main.py upgrade --env=dev`。dev 环境启动时会自动生成迁移并**删除含破坏性 `DROP` 的产物**，需人工确认生成的迁移内容再提交。
3. 种子：新增 `backend/sql/image_workbench_{service,model}.json` 与 `sys_menu.json` 的菜单子树；`initialize.py` 增加 FK 解析与一条菜单合并调用。
4. 前端：新增 API 层与两个页面，补 i18n 双份词条，`defineOptions({ name })` 与 `route_name` 对齐。
5. **必须重启后端**——动态路由发现（`app/core/discover.py`）只在启动时执行，不重启新模块不生效且会静默失败。
6. 验证：`cd backend && uv run pytest`；`cd frontend/web && pnpm ts:check && pnpm lint && pnpm test`。构建产物同步见 `SKILL.md` 第 9 节（三份 dist 必须重建并同步，`--delete`）。

**回滚策略**：`uv run main.py downgrade -1` 撤销迁移会删除三张表及其种子数据；同时手工删除 `sys_menu` 中 `route_name = ImageWorkbench` 的整棵子树（迁移不处理业务菜单行）。前端回滚到上一版 dist。因为本变更对上游**全程只读**、不修改任何上游状态，回滚无上游副作用。

## Open Questions

- **apiworld 的 OpenAI 兼容层能否接受内联 `image` 字段**：影响管理员为 apiworld 选哪个接入方式，配置时实测即知。代码支持两种，不影响规格与任务拆分。
- **apiworld 侧是否有可用的图片编辑端点**：若没有，则 apiworld 的参考图路径不可用，需在能力声明里关掉 `support_reference`。属配置侧结论，同样不影响本方案结构。
