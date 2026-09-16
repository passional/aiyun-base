# Proposal: 图片生成工作台（image-workbench）

## Why

平台当前只具备"调用—测量"的**渠道评估**能力，没有任何面向业务用户的图片生成能力。而现实里已经有两条可用的上游：火山方舟 Agent Plan（Seedream 5.0 pro / lite）与 apiworld。今天要让用户用上它们，只能靠每用户自带的 Redis 模型配置（`base_url` + `api_key` + `model_id`，7 天 TTL、聊天专用），凭证与可用模型完全不受管理员管控，也无法纳入 RBAC。

火山方舟的 Seedream 接口**形似 OpenAI**——同为 `images/generations` 路径、`Authorization: Bearer`、`data[].url` / `data[].b64_json` 响应——但有三处实质不同：参考图是同一端点里一个普通 **JSON 字段**而非独立的 `/v1/images/edits` multipart 端点；`size` 收 `1K`/`1.5K`/`2K` 这类**分辨率档位**而非 OpenAI 的固定像素枚举；另有一批厂商扩展字段。直接套用现成的 OpenAI 图片客户端，图生图与尺寸两条路都会走不通。且同一模型的 ID 在不同入口下写法不同（Agent Plan 是 `doubao-seedream-5-0-pro`，标准 API 是 `doubao-seedream-5-0-pro-260628`），能力档位也逐模型不同（pro 为 `1K/1.5K/2K`，lite 为 `2K/3K/4K`）。

因此需要：管理员在后台配置绘图服务与绘图模型（含逐模型的能力声明），登录用户在工作台上做单次生成并保留历史，整块功能纳入平台权限分配体系。

## What Changes

- 新增**绘图服务配置**（管理员）：与「平台」平级的独立实体，由接入方式、base_url、凭证与可用模型定义。首期两个：火山方舟 Agent Plan（接入方式「火山方舟」，base_url `https://ark.cn-beijing.volces.com/api/plan/v3`）与 apiworld（接入方式「OpenAI 兼容」）。base_url 填**完整 API 前缀**（含 `/v1` 或 `/api/plan/v3`），与「平台」的 base_url 语义刻意不同。
- 新增**绘图模型配置**（管理员）：绘图服务下的两级结构，持有模型 ID 与**逐模型的能力声明**——可选尺寸档位、是否支持参考图及张数上限、输出格式、水印默认值、默认参数透传。能力声明可配而非代码硬编码，以容纳模型 ID 与档位的漂移。种子预置火山 pro / lite 两条能力声明（不含任何凭证）。
- 新增**绘图工作台**（登录用户）：单次生成，一次任务产出**一张图**。支持文生图与参考图（用户上传，后端转 base64 内联或走 multipart，按接入方式决定）。失败**不自动重试**，提供一键手动重试。每用户同时最多 1 个进行中的绘图任务，并提供取消（语义为"放弃等待"，不等于不产生上游费用）。
- 新增**绘图任务**实体：提交即返回，后端异步执行，SSE 推进度。上游返回的图片 URL **24 小时后失效**，因此后端 MUST 在拿到结果时立即下载转存到存储节点，不得只存上游链接。历史记录默认仅本人可见，删除为软删记录 + 真实删除存储文件。
- 新增**"绘图工作台"一级菜单**（含工作台与绘图服务配置两个子菜单）与 `image_workbench:*` 权限码，纳入标准 RBAC；存量库经 `scripts/initialize.py` 的幂等菜单合并补齐。

## Capabilities

### New Capabilities

- `image-workbench/access`: 权限体系接入——"绘图工作台"一级菜单树、`image_workbench:*` 三段式权限码、前后端一致的字面量约定、绘图任务的数据范围（默认仅本人）、存量库的幂等菜单补种。
- `image-workbench/service-config`: 绘图服务与绘图模型配置——绘图服务（名称 / 接入方式 / 完整 base_url / 加密凭证）的增删改查与连通性测试、两级结构下的绘图模型（模型 ID / 能力声明 / 启用）、接入方式对请求形态的影响、能力声明的语义与预置。
- `image-workbench/generation`: 绘图任务的提交与执行——参数校验（按模型能力声明收敛）、参考图上传与两种上行形态、异步执行与 SSE 进度、产物立即转存（应对 24 小时 URL 失效）、用量记录、失败与手动重试、并发闸门与取消、历史记录的可见范围与删除、下载。

### Modified Capabilities

（无——`admin-console/*` 与 `channel-eval/*` 的行为均不改变。）

## Impact

- **backend**：新增 `app/modules/image_workbench/`（`service` / `model` / `generation` 等功能，含 controller/service/crud/model/schema 与模块根 `plugin.toml`）；`app/api/v1/routers.py` 注册路由；新增绘图服务、绘图模型、绘图任务三张表，需 Alembic 迁移；`backend/sql/*.json` 增加菜单种子与火山 pro / lite 能力声明种子；`app/scripts/initialize.py` 增加一次 `__ensure_seed_menu_merged()` 调用；复用 `core/sse_manager`（进度推送）、`CryptoUtil`（凭证加密）、`task_storage` 的存储适配层（产物落盘，首期用默认本地节点）、`utils/upload_util`（参考图与产物的文件写入）、httpx（上游调用）。
- **frontend-web**：新增 `api/module_image_workbench/`、`views/image_workbench/`（工作台页、绘图服务配置页及其私有组件）；i18n 词条 `zh.json` / `en.json` 双份；页面组件名与菜单 `route_name` 对齐；工作台页有 SSE 订阅，需实现 `onActivated` / `onDeactivated` 生命周期。
- **frontend-app / docs / docker**：无影响（本变更只做 web 端）。
- **外部系统**：火山方舟 Agent Plan（`/api/plan/v3/images/generations`，专属 Base URL 与专属 API Key，混用会调用失败或产生额外费用）、apiworld。本变更对上游**全部为只读调用**——不创建令牌、不改渠道配置、不写任何上游状态。
- **决策记录**：设计阶段已落 `docs/adr/0004`（绘图服务不复用「平台」）、`docs/adr/0005`（绘图服务 base_url 填完整前缀）与 `CONTEXT.md` 的三个术语（绘图服务 / 绘图模型 / 绘图任务），实现须遵循。