# 语音工作台设计（ASR 首版）

状态：已实现（2026-11-03 设计确认，2026-11-04 落地）。

本文件记录语音工作台首版的产品与实现设计。相关的架构取舍见 ADR-0010、ADR-0011、ADR-0012；领域术语见 `GLOSSARY.md`。

## 1. 背景与范围

系统需要接入火山引擎方舟 Agent Plan 的语音识别模型（豆包流式语音识别 2.0），让管理员在后台配置 key 与模型，并在工作台上传音频得到转写结果。

首版范围：

- 新模块 `voice_workbench`，仅 Web 管理端。
- 只做语音识别（ASR），不做语音合成（TTS）。
- 两个页面：语音工作台（转写与历史）、语音服务配置（服务与模型）。
- 支持批量选择音频，一次最多 10 个，逐文件顺序执行。
- 转写任务落库；原音频不落盘。

## 2. 非目标

- 不做 TTS、语音对话、实时麦克风输入。
- 不做移动端（`frontend/app`）适配。
- 不做异步任务队列、断点续传、跨设备任务恢复。
- 不做上游模型列表探测或主动连通性测试。
- 不保存原始音频，因此不提供原音频回听或重新识别。

## 3. 术语

见 `GLOSSARY.md` 的「语音识别」段：语音服务、语音识别模型、转写任务、转写文本、转写分段、批量转写、原始音频。

## 4. 总体结构

```text
frontend/web
  views/voice_workbench/job/index.vue      语音工作台
  views/voice_workbench/config/index.vue   语音服务配置
        │
        ▼
backend/app/modules/voice_workbench
  config/  service + model 的 CRUD、凭证加密与脱敏
  job/     转写接口、WAV 校验、上游 WebSocket 客户端、任务记录
        │
        ▼
火山引擎 Agent Plan ASR
  wss://openspeech.bytedance.com/api/v3/plan/sauc/bigmodel_async
```

语音服务与模型的分层对齐绘图工作台：

- 语音服务：管理员可配的上游凭证容器，一条服务一个 key。
- 语音识别模型：挂在服务下，携带上游模型标识、资源标识、完整端点 URL 与识别限额。

## 5. 配置模型

### 5.1 语音服务表 `voice_workbench_service`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `name` | String(100) | 服务名称，唯一 |
| `api_key_enc` | String(512) | 调用凭证密文（Fernet） |
| `status` | Integer | 0 启用 / 1 停用 |
| `remark` | String(500) | 备注 |

### 5.2 语音识别模型表 `voice_workbench_model`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `service_id` | Integer FK | 所属语音服务，删除保护用 RESTRICT |
| `display_name` | String(100) | 工作台展示名 |
| `model_name` | String(128) | 上游请求体里的 `model_name`，原样提交 |
| `resource_id` | String(128) | 上游请求头 `X-Api-Resource-Id` |
| `endpoint_url` | String(512) | 完整 WebSocket 端点，管理员可改 |
| `max_file_mb` | Integer | 单文件大小上限（MB） |
| `max_duration_sec` | Integer | 单文件音频时长上限（秒） |
| `status` | Integer | 0 启用 / 1 停用 |
| `remark` | String(500) | 备注 |

不设 `provider` / `type` 字段：首版只有火山引擎、只有 ASR。将来接入 TTS 或其它供应商时通过迁移新增字段或新实体（见 ADR-0010）。

### 5.3 凭证约定

沿用绘图服务的约定：

- 创建/编辑入参接收明文 `api_key`，字段名命中操作日志的敏感键集合，日志自动脱敏。
- 任何出参不回显明文或密文，只回显 `api_key_masked`（`••••` + 末 4 位）与 `has_api_key`。
- 编辑时留空表示沿用原凭证。
- 服务被模型引用、模型被转写任务引用时拒绝删除，提示改为停用。

### 5.4 默认值与种子

预置一条语音服务与一个语音识别模型，key 留空，管理员只需填写 key：

| 配置项 | 默认值 |
| --- | --- |
| 服务名称 | 火山引擎语音 |
| 模型展示名 | 豆包流式语音识别 2.0 |
| `model_name` | `bigmodel` |
| `resource_id` | `volc.seedasr.sauc.duration` |
| `endpoint_url` | `wss://openspeech.bytedance.com/api/v3/plan/sauc/bigmodel_async` |
| `max_file_mb` | 20 |
| `max_duration_sec` | 300 |

## 6. 工作台

### 6.1 模型选择

- 顶部下拉列出「服务启用、模型启用、服务已配置 key」的语音识别模型。
- 只有一个可用模型时自动选中；没有可用模型时提示先到配置页完成配置。
- 用户可见的只有模型的展示名。

### 6.2 文件与批量

- 仅文件上传，不做浏览器录音。
- 一次最多选择 10 个文件；超出时提示分批上传。
- 支持点击选择与拖拽上传。
- 选中后形成待处理列表，每个文件独立一行，显示文件名、大小、状态与结果。

### 6.3 浏览器转码

按 ADR-0011：

1. 用 `AudioContext.decodeAudioData` 解码文件。
2. 用 `OfflineAudioContext` 重采样为 16kHz、单声道。
3. 编码为 PCM16 WAV（44 字节头 + 采样数据）。
4. 上传转码后的 WAV，任务记录保留原始文件名。

浏览器无法解码时，该文件直接标记失败，错误为「浏览器无法解码该音频」，不发起请求。

### 6.4 执行与结果

- 前端顺序执行：一个文件完成后再发下一个，不并行。
- 每行状态：待处理 / 转码中 / 识别中 / 成功 / 失败。
- 成功后展示转写文本与分段（起止时间 + 文本）。
- 支持复制全文、导出 `.txt`（文件名取原音频名）。
- 失败行显示上游或校验错误，支持单独重试。
- 批量中某个文件失败不影响其它文件。

### 6.5 历史

- 历史列表按用户隔离，分页展示。
- 字段：原文件名、大小、时长、模型、状态、耗时、创建时间。
- 详情展示转写文本与分段，支持复制、导出。
- 支持单条与批量软删除；没有音频文件需要清理。

## 7. 后端执行

### 7.1 请求流程

`POST /voice_workbench/jobs/transcribe`（multipart：`file`、`model_id`）：

1. 获取每用户串行锁；已有一个在途请求时返回业务错误「有正在进行的识别任务」。
2. 校验模型启用、服务启用、服务已配置 key。
3. 校验文件大小不超过模型的 `max_file_mb`。
4. 校验 WAV：RIFF/WAVE、PCM、16kHz、16bit、单声道；解析 data 长度并计算时长，不超过 `max_duration_sec`。
5. 创建转写任务记录，状态 `running`，记录原始文件名、大小、时长、服务与模型快照。
6. 连接上游 WebSocket，发送全量请求与音频分片，收集结果。
7. 成功：写入转写文本、分段、耗时、用量（若上游返回），状态 `success`。
8. 失败：写入错误信息，状态 `failed`；接口返回业务错误。
9. `finally` 释放串行锁。

### 7.2 上游协议

按官方示例实现二进制协议：

- 握手头：`X-Api-Key`、`X-Api-Resource-Id`、`X-Api-Request-Id`、`X-Api-Connect-Id`、`X-Api-Sequence: -1`。
- 全量请求（JSON + gzip）：
  - `user.uid`：当前用户 ID。
  - `audio`：`format=wav`、`codec=raw`、`rate=16000`、`bits=16`、`channel=1`。
  - `request`：`model_name` 取模型配置；`enable_itn`、`enable_punc`、`enable_ddc`、`show_utterances` 固定为 true；`enable_nonstream=false`。
- 音频分片：PCM 数据按固定大小切片，gzip 压缩；最后一片使用负序号与 `NEG_WITH_SEQUENCE`。
- 分片发送节奏：不按示例的 200ms 实时节奏，尽快发送，保留一个可调的小间隔常量（初始 10–20ms）。
- 响应解析：处理 `SERVER_FULL_RESPONSE` 与 `SERVER_ERROR_RESPONSE`，按位标志读取序号、事件、payload 长度，gzip 解压后解析 JSON。
- 结果聚合：收集 `utterances`（起止时间与文本），最终文本以完整响应为准。

### 7.3 串行锁

Redis key：`voice_workbench:transcribe:lock:{user_id}`，带过期时间（覆盖最长请求时长）。获取失败返回业务错误；请求结束在 `finally` 中释放。不同用户互不影响。

### 7.4 错误处理

- 网关/连接错误、上游 `code != 0`、上游错误响应：任务标记 `failed`，错误信息保留可读原因。
- 不做自动重试；用户在上传列表里手动重试。
- 服务重启后遗留的 `running` 任务在启动清理时标记为 `failed`（原因：服务重启导致任务中断）。

### 7.5 任务记录表 `voice_workbench_job`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `service_id` | Integer FK | 所属语音服务（RESTRICT） |
| `model_pk` | Integer FK | 语音识别模型（RESTRICT） |
| `model_name` | String(128) | 提交时的上游模型标识快照 |
| `original_filename` | String(255) | 原始文件名 |
| `file_size` | Integer | 音频字节数 |
| `duration_sec` | Integer | 音频时长（秒） |
| `text` | Text | 转写文本 |
| `segments_json` | JSON | 转写分段（起止时间与文本） |
| `status` | String(16) | `running` / `success` / `failed` |
| `error` | Text | 失败原因 |
| `elapsed_ms` | Integer | 耗时 |
| `usage_json` | JSON | 上游用量（若有） |
| `started_at` | DateTime | 开始时间 |
| `finished_at` | DateTime | 结束时间 |

按 `created_id` 做用户隔离（`UserMixin`）。不存音频路径。

## 8. API 契约

配置：

| 方法 | 路径 | 权限 |
| --- | --- | --- |
| GET | `/voice_workbench/services/list` | `voice_workbench:service:query` |
| GET | `/voice_workbench/services/optionselect` | `voice_workbench:service:query` |
| GET | `/voice_workbench/services/detail/{id}` | `voice_workbench:service:query` |
| POST | `/voice_workbench/services/create` | `voice_workbench:service:add` |
| PUT | `/voice_workbench/services/update/{id}` | `voice_workbench:service:edit` |
| DELETE | `/voice_workbench/services/delete` | `voice_workbench:service:delete` |
| GET | `/voice_workbench/models/list` | `voice_workbench:service:query` |
| GET | `/voice_workbench/models/optionselect` | `voice_workbench:service:query` |
| POST | `/voice_workbench/models/create` | `voice_workbench:service:add` |
| PUT | `/voice_workbench/models/update/{id}` | `voice_workbench:service:edit` |
| DELETE | `/voice_workbench/models/delete` | `voice_workbench:service:delete` |

转写：

| 方法 | 路径 | 权限 |
| --- | --- | --- |
| POST | `/voice_workbench/jobs/transcribe` | `voice_workbench:job:add` |
| GET | `/voice_workbench/jobs/list` | `voice_workbench:job:query` |
| GET | `/voice_workbench/jobs/detail/{id}` | `voice_workbench:job:query` |
| DELETE | `/voice_workbench/jobs/delete` | `voice_workbench:job:delete` |

统一使用 `SuccessResponse` / `ResponseSchema`、`OperationLogRoute` 与 `CustomException`；路由注册进 `app/api/v1/routers.py` 的 `"/voice_workbench"`。

## 9. 权限与菜单

- 父菜单：语音工作台。
- 子菜单「语音转写」：`component_path = voice_workbench/job/index`，`route_name = VoiceWorkbenchJob`，`route_path = job`。
- 子菜单「语音服务配置」：`component_path = voice_workbench/config/index`，`route_name = VoiceWorkbenchConfig`，`route_path = config`。
- `defineOptions({ name })` 必须与 `route_name` 一致。

## 10. 前端结构

```text
src/api/module_voice_workbench/
  service.ts
  model.ts
  job.ts
src/views/voice_workbench/
  job/
    index.vue
    audio.ts              解码、重采样、WAV 编码（纯函数）
    components/ResultPanel.vue
    components/HistoryPanel.vue
  config/
    index.vue             左服务、右模型
    components/ModelPanel.vue
```

- 音频转码逻辑写成纯函数并配 Vitest 单测。
- 批量队列逻辑与 UI 分离，便于测试。
- 所有文案进 `locales/langs/zh.json` 与 `en.json`，使用 `$t()`。
- 列表页遵循 `design/UI-GUIDELINES.md`：`FaTable`、`FaDialog`、16px 间距、12px 圆角。

## 11. 测试

后端：

- 配置：凭证 Fernet 密文落库、出参只回显掩码、编辑留空保留原值、删除保护。
- 音频校验：非 WAV、错误采样率/位深/声道、超大小、超时长。
- 协议：全量请求头字节、分片序号与末片标志、响应帧解析（含 gzip）用固定样例。
- 转写接口：mock 上游成功写任务、上游错误标记失败、模型停用/服务无 key 拒绝、串行锁冲突、用户隔离。
- 迁移：autogenerate 零差异。

前端：

- 重采样与 WAV 编码：采样率、声道、头长度、数据长度。
- 批量队列：顺序执行、单项失败不影响后续、重试。
- `pnpm ts:check`、`pnpm lint`、`pnpm test`。

## 12. 风险与待验证

1. **上游发送节奏**：示例按实时节奏发片；改为尽快发送后是否被上游限速或拒绝，需用真实 key 验证。分片间隔做成常量，便于调整。
2. **5 分钟与 300s 网关**：需实测上游处理速度；若接近网关上限，应下调默认时长或改异步。
3. **浏览器解码范围**：个别编码无法解码，前端明确报错，不做服务端兜底。
4. **上游标识**：`model_name=bigmodel` 与 `resource_id=volc.seedasr.sauc.duration` 来自文档与需求说明，均可后台修改。
5. **批量耗时**：10 个 5 分钟文件顺序执行总耗时可观，界面必须逐行显示进度。

## 13. 实施顺序

1. 后端模型、迁移、种子数据。
2. 配置 CRUD（加密、掩码、删除保护）。
3. 上游 WebSocket 客户端与协议单测。
4. 转写接口（校验、串行锁、任务记录）。
5. 前端 API 与配置页。
6. 工作台页面（转码、批量、结果、历史）。
7. 菜单、权限、i18n。
8. 全量测试与手工验收。
