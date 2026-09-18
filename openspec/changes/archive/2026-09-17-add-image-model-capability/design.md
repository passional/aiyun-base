## Context

动机见 `proposal.md`。此处只列影响方案的现存事实与约束。

**基座已归档**：`openspec/changes/archive/2026-09-17-add-image-workbench/`，主 spec 在 `openspec/specs/image-workbench/`。本变更全部建立在它之上。

**能力声明今天的样子**：`ImageWorkbenchCapabilitySchema`（`config/schema.py:122-157`）是八个固定字段——`sizes`、`allow_custom_size`、`support_reference`、`max_reference_images`、`output_formats`、`default_output_format`、`watermark_default`、`extra_params`。前端只拿它取**选项集**，表单控件是手写的（`views/image_workbench/job/index.vue:427-466`）。

**上游请求体是显式拼的**：`build_ark_payload` / `build_openai_payload`（`job/client.py:193-241`）逐字段列名，再由 `_merge_extra_params`（`job/client.py:181-191`）用能力声明里的 `extra_params` 打底、显式字段覆盖。`response_format` 是模块常量 `_RESPONSE_FORMAT = "url"`（`job/client.py:30`），不是参数。

**任务是单产物的**：`image_workbench_job`（`job/model.py:18-58`）只有 `image_path` 与 `image_size` 两个产物字段，产物落 `image_workbench/{yyyy}/{mm}/{job_uuid}.{ext}`（已归档 design D5）。

**参考图上传已按真实内容校验**：`POST /image_workbench/jobs/reference-images/upload`（`job/controller.py:48-67`）调用 `validate_reference_image_bytes` 读文件头判格式，说明上传时读图片头已是既有做法。

**约束一：后端没有图像处理库。** `backend/pyproject.toml` 的依赖里没有 Pillow 或同类库。

**约束二：上游参数面靠文档委托推断。** Agent Plan 的文档《接入视觉模型》把参数列表委托给标准 Seedream 文档，并声明无按前缀的参数差异；`layer_decomposition` 与 `/api/plan/v3` 从未在任何一页文档中同时出现。它是本次唯一的**未实测**核心能力，且图层拆分同步返回 `data[]`（上游没有轮询接口）。**8.1 已实测确认它在 `/api/plan/v3` 上被接受**，见「实测回填」。

**约束三：上游默认值与产品默认值相反。** `watermark` 上游默认 `true`，而产品要默认不加水印（seed 的 `watermark_default: false`）。因此**不能依赖上游默认值**，未显式传的字段会得到与预期相反的结果。

## Goals / Non-Goals

**Goals:**

- 出图参数面成为**数据**：模型支持哪些参数、取值范围、默认值、何时可用，全部由能力声明承载，前后端都读它。
- 图层拆分可用，且一次任务的多产物在历史记录里逐张可辨、可分别下载、随任务一并移除。
- 水印由用户决定（ADR-0008）。
- 清单里的每个取值都标出**来源**，未实测的取值在界面上可辨认。

**Non-Goals（设计层面，不重复 proposal 的范围）:**

- 不做清单的可视化编辑器之外的通用表达式引擎（ADR-0006 的取舍）。
- 不引入 `or` / `not` 这类条件组合——已知规则全是合取。
- 不把产物被引用时的删除保护写进本变更：引用关系由下一个变更引入，保护随之在那时进入规格。
- 不为清单建立版本号或迁移框架：清单是逐模型的数据，改错了直接改数据。

## Decisions

### D1 能力声明的结构：一份参数条目清单

`capability_json` 的存储位置不变（`image_workbench_model.capability_json`），内部结构整体替换：

```jsonc
{
  "reference": { "supported": true, "max_images": 14 },   // 原 support_reference / max_reference_images 迁入
  "modes": [                                              // 该模型支持的出图方式
    { "key": "layer_decomposition",
      "label": { "zh": "图层拆分", "en": "Layer decomposition" },
      "trigger": { "param": "layer_decomposition", "eq": true } }
    // trigger 可以是 null：表示该出图方式不由参数触发，而是该模型固有的能力位。
    // 交互编辑（下一个变更）就是这样一种方式——上游没有对应参数，它靠提示词里的坐标
    // 标记表达，因此不可能从参数值派生。trigger 为 null 时该方式只用于前端决定是否
    // 提供入口，不参与 D3 的派生校验。
    // 不使用坐标的出图方式（本变更的图层拆分就是）**不需要**声明任何坐标标记语法；
    // 只有靠坐标表达位置的出图方式才需要声明占位形态与标记写法（见下一个变更）。
  ],
  "params": [
    {
      "key": "size",                                      // 同时是上游请求体的字段路径
      "label": { "zh": "尺寸", "en": "Size" },
      "control": "select",                                // select / multiselect / switch / number / text
      "allowed": ["1K", "1.5K", "2K"],
      "allow_custom": false,                              // select 时允许自由填写（原 allow_custom_size）
      "default": "2K",
      "source": "documented",                             // documented / inferred / measured
      "when": null,                                       // 条件依赖，见 D2
      "overrides": {                                      // 按出图方式覆盖本条目
        "layer_decomposition": { "allowed": ["1K", "1.5K", "2K", "auto"], "default": "auto" }
      }
    }
    // … layer_decomposition / output_format / background / watermark /
    //    optimize_prompt_options.mode / response_format
  ]
}
```

**理由**：参数的全部知识集中在一处，加模型与加参数都是改数据。`control` 是前端的渲染指令，`allowed`/`default` 是校验依据，两者同源，不会各说各话。

**代价**：清单写错要到运行期才暴露，因此存储时必须做结构校验（Pydantic），且提交时按同一份清单校验而不是另写一套。

### D2 条件依赖用封闭词表，不做表达式

`when` 是一个合取式，左值只有两种、算子只有四种：

```jsonc
"when": { "all": [
  { "param": "output_format", "not_in": ["jpeg"] },       // 左值＝另一个参数的当前值
  { "request": "reference_count", "eq": 1 },              // 左值＝本次请求的事实
  { "request": "reference_has_alpha", "eq": true }
]}
```

- 左值：`{"param": "<参数键>"}` 或 `{"request": "<请求事实>"}`。请求事实的集合**封闭**，只有 `reference_count`（整数）与 `reference_has_alpha`（布尔）。
- 算子：`eq` / `ne` / `in` / `not_in`。
- 组合：只有 `all`。

**理由**：今天已知的全部规则都是合取，且每条左值都是这两类之一。`or` 与 `not` 一旦引入，等于开始写一个小型表达式语言——那正是 ADR-0006 刻意推迟的东西。请求事实集合封闭，是为了让前端能在不认识新事实时安全地把它当作不满足（保守置灰），而不是猜。

### D3 出图方式由参数值触发，不单独提交

请求里**没有** `mode` 字段。清单用 `modes[].trigger` 声明「哪个参数取哪个值即进入哪种出图方式」，参数的 `overrides` 再按出图方式覆盖取值范围与默认值。

**理由**：上游那边图层拆分就是一个布尔字段，不是另一个端点。若在提交契约里再引入一个 `mode`，就造出了「`mode` 与参数值不一致」这一整类需要额外校验与同步的状态，而它们必然会被写歪。让出图方式是**派生结果**，不一致就不存在。

### D4 参数键就是上游字段路径，点号表示嵌套

`key` 直接作为上游请求体的字段路径：`size` → `{"size": ...}`；`optimize_prompt_options.mode` → `{"optimize_prompt_options": {"mode": ...}}`。组装器按点号逐层建字典。

`model`、`prompt` 不是参数（由任务的固定字段提供）；参考图不是参数（由参考图机制提供）。

**理由**：参数与上游字段一一对应，少一层「本地名 → 上游名」的映射表；映射表是漂移的温床。代价是参数键里会出现点号，前端提交的 `params` 用点号作键。

**参数的适用性也由清单决定，不由接入方式决定。** 接入方式决定的是**参考图的上行形态**（既有规格已定），它 MUST NOT 再决定「哪些参数发得出去」。一个 OpenAI 兼容的模型，其清单里就不声明 `output_format` 与 `watermark` 两个条目——那两个是火山方舟的厂商扩展字段。这样客户端不需要按接入方式过滤参数，D5 的「快照等于实际发送的参数集」也就自然成立；两者不会各留一条「该发不该发」的规则。**种子只给火山方舟服务下的模型写清单**；将来新增 OpenAI 兼容的绘图服务时，管理员按其接口的真实能力声明参数面。

### D5 提交契约：开放参数字典，服务端补齐默认值

```
POST /image_workbench/jobs          权限 image_workbench:job:create
{
  "model_pk": 1,
  "prompt": "…",
  "params": { "size": "2K", "layer_decomposition": true },   // 只传用户改过的；未传的由服务端按清单补默认值
  "reference_images": ["…"]
}
```

服务端顺序：

1. 取该模型的清单。
2. 求值：算出**当前请求条件下**每个参数是否可用。`when` 的求值需要参数值与请求事实——用户显式提交的值直接参与，未提交的参数用清单默认值参与（求值时用默认值代入，不等于要把默认值写进最后的结果）。
3. 校验**用户显式提交**的每一个参数：键必须存在于清单、值必须在取值域内、且该参数在当前条件下必须可用；任一不成立即拒绝。
4. 为**当前条件下可用、且用户未提交**的参数补上清单默认值。
5. 得到一份完整且已解析的参数集 → 落 `params_json` 快照 → 发上游。

**关键约束：只给可用的参数补默认值。** 当前条件下不可用的参数 MUST NOT 被补默认值、MUST NOT 发往上游、MUST NOT 进快照。反过来做的后果很具体：`background` 的可用性要求「参考图恰好一张且带透明通道」，若先无条件补齐再校验，**每一次没传参考图的普通请求**都会被补上 `background` 的默认值，随即被它自己的可用性条件拒掉——一条默认值就能把整条生成路径堵死。

**理由**：快照必须等于实际发给上游的内容（与 `model_id` 采用快照同一取向，见已归档 design D2）。补齐在服务端做，前端只负责「用户改过的」，两边不会对「默认值到底是几」产生分歧。**这也直接解掉约束三**：`watermark` 未传时由清单补 `false` 而不是让上游按自己的 `true` 生效。

### D6 `extra_params` 移除

清单里不再有 `extra_params`。原本要靠它透传的字段，一律写成显式参数条目。

**理由**：它允许管理员设置用户看不见的上游字段，用户选的参数与实际上行不一致时排查成本极高（`_merge_extra_params` 的注释已经写出了这层担忧）——这正是本变更要消灭的盲区。迁移时若库内该字段非空，写一条警告日志列出被丢弃的键，不静默吞掉。

### D7 产物独立建表，任务表去掉产物字段

```
image_workbench_artifact
  id / job_id(FK→image_workbench_job, ondelete=CASCADE) / z_index / name / description /
  bounding_box_json / file_path / image_size / output_format / 公共审计列
```

- `z_index` **可空**：底图为 `NULL`，图层存上游给的层级值。底图不是图层（见 `CONTEXT.md`），用「无层级」而非「层级 0」表达这一点，查询「取底图」就是 `z_index IS NULL`。
- `bounding_box_json` 存上游的位置框（绝对与归一化两种表示都留），交互编辑将直接取归一化那份作为坐标标记——两者是同一个 0–999 坐标系。
- **`image_workbench_job.image_path` 与 `image_size` 删除**。两处记录「这次的产物是哪个文件」必然漂移。

**理由**：多产物是这次的核心，把它塞进 JSON 列会让「按图层建立引用」「按图层查询」无处落脚（下一个变更就要用），也让下载与删除只能整包处理。

### D8 产物文件按任务分目录，迁移不搬动既有文件

新任务的产物落 `image_workbench/{yyyy}/{mm}/{job_uuid}/`：底图 `base.{ext}`、图层 `layer_{z_index:02d}.png`（图层恒为 PNG）。

`file_path` 是权威，目录布局只是约定。**迁移不移动既有文件**——存储节点可能是远端协议（ftp/sftp/s3/obs/oss/cos），搬文件不可行也没有必要。既有的单产物任务由迁移写成一行产物，`file_path` 沿用旧路径。

### D9 透明通道的判定放在上传时，用无依赖的头部解析

`POST /image_workbench/jobs/reference-images/upload` 的响应增加 `has_alpha`。判定方式：

- **PNG**：IHDR 的 colour type 为 4 或 6 → 有透明通道；为 3（调色板）时再找 `tRNS` 块。
- **WebP**：`VP8X` 的 alpha 标志位，或 `VP8L` 头部的 alpha 位。
- **JPEG / GIF**：判为无。

**理由**：后端没有图像处理库（约束一），只为读一个标志引入 Pillow 不划算；判定必须发生在**上传时**，因为规格要求「参数可用性随本次请求的输入变化」在上传后即可见，而不是等到提交。GIF 判为无是因为上游未把 GIF 列为该能力的可接受输入（索引透明是否被接受见 Open Questions）。

### D10 种子的改写与修正

- lite 的参考图上限由 10 改为 **14**（上游文档：pro 上限 10、lite 上限 14）。
- 两个模型按新结构重写。`size` / `output_format` / `watermark` / `response_format` / `optimize_prompt_options.mode` 标 `documented`；`layer_decomposition` 与 `background` 初标 **`inferred`**（参数本身有文档，但它在这条 base_url 上的可用性从未被任何文档同时确认），8.1 实测确认被接受后**回填为 `measured`**（见「实测回填」）。
- `watermark` 默认值维持 `false`（产品选择，ADR-0008），并在种子备注里写清上游默认是 `true`。
- 模型标识维持当前写法不擅自改写，初标 `inferred` 等实测；8.1 的实测请求即用该标识发出并被接受，标识因此确认可用（仍保留备注提示管理员以实际入口为准）。

### D11 API 契约变更一览

| 端点 | 权限 | 变更 |
|---|---|---|
| `POST /image_workbench/jobs` | `image_workbench:job:create` | 请求体由具名参数改为 `params` 字典（D5） |
| `GET /image_workbench/jobs/detail/{id}` | `image_workbench:job:query` | 响应增加 `artifacts[]`（含层级、名称、描述、位置框、尺寸、格式） |
| `GET /image_workbench/jobs/{id}/artifacts/{artifact_id}/download` | `image_workbench:job:download` | **新增**，按产物逐张下载 |
| `GET /image_workbench/jobs/{id}/download` | `image_workbench:job:download` | **移除**，由上面这条取代 |
| `POST /image_workbench/jobs/reference-images/upload` | `image_workbench:job:create` | 响应增加 `has_alpha` |
| `GET /image_workbench/jobs/models/optionselect` | `image_workbench:job:query` | `capability` 换成新结构 |
| 绘图模型的创建 / 编辑 / 详情 | `image_workbench:model:*`（沿用既有码） | `capability` 换新结构 |

权限码不变，不新增菜单与按钮权限。

## Risks / Trade-offs

- **[清单是数据，写错要到运行期才暴露]** → 存储时 Pydantic 结构校验拒绝不合规的声明（规格已把它写成可观察行为）；提交时按同一份清单校验；每个取值带来源标记，未实测的在界面上可辨认。tasks 里另有一条针对 `layer_decomposition` 的实测确认任务。
- **[`layer_decomposition` 在 `/api/plan/v3` 上未实测]** → 若实测被上游拒绝，影响面限于**清单数据**与产物转存（把该参数与 `modes` 条目从种子里去掉即可），不触及清单结构、提交契约与产物表。这是本次最重的不确定性，也是刻意把它隔离在数据层的原因。**已解除**：8.1 实测确认 `layer_decomposition` 与 `background` 均被接受，种子的来源标记已回填为 `measured`。
- **[默认值必须由服务端补齐，否则与上游默认值相反]** → `watermark` 上游默认 `true`、产品默认 `false`，漏传即得到相反结果。D5 规定补齐在服务端做，前端只提交用户改过的键。
- **[产物表让列表页的缩略图多一次关联]** → 列表取每任务的底图（`z_index IS NULL`）作为缩略；任务详情才展开全部产物。
- **[`optimize_prompt_options.mode` 的默认档位无文档依据]** → 上游只写了取值与「`fast` 不支持 lite/4.5」，没写默认是哪个。种子按 `standard` 填并标 `documented`。**已解除（部分）**：8.1 实测确认默认档位就是 `standard`，与种子一致，无需改数据；「是否还有其他档位」本次未继续穷举——取值域保持 `["standard", "fast"]`，若将来发现新档位，属纯数据改动（管理员改清单或改种子）。
- **[清单结构变更是一次性破坏性改动]** → 见 Migration Plan；本变更不做读时兼容层，只做一次数据迁移。

## Migration Plan

**Alembic 迁移（一次）**

1. 建 `image_workbench_artifact` 表。
2. 把既有 `image_workbench_job` 中 `image_path` 非空的行，各写成一行产物：`z_index = NULL`、`file_path` 沿用 `image_path`、`image_size` 沿用、`output_format` 由扩展名推断（无法推断时留空）。**不移动文件**（D8）。
3. 删除 `image_workbench_job.image_path` 与 `image_size`。
4. **数据部分**：把 `image_workbench_model.capability_json` 的旧结构逐行转成新结构——旧字段映射为参数条目（`sizes` → `size` 条目、`output_formats` → `output_format` 条目、`support_reference`/`max_reference_images` → `reference` 块、`watermark_default` → `watermark.default`、`allow_custom_size` → `size.allow_custom`）；来源统一标 `documented`；`layer_decomposition`/`background` 条目**不生成**（旧结构里没有，需管理员按新种子重填或重建库）。`extra_params` 非空时打警告日志。

**种子与存量库的差异（须记住）**：`backend/sql/*.json` 只在**全新库**初始化时导入，已存在的库不会自动拿到修正后的种子值（例如 lite 的参考图上限 14）。存量库要么由管理员手改，要么重建。这是既有机制的固有行为，本次不改。

**回滚**

`uv run main.py downgrade -1`：重建 `image_path` / `image_size` 两列，从每任务**层级最小**的产物回填；清单结构由新结构反推回旧结构。

**回滚是有损的**，必须先知情：多产物任务的其余产物行会随表被删（文件留在存储节点上成为孤儿，降级前应先确认无多产物任务）；条件依赖与来源标记在旧结构里无处存放，会丢失。因此**降级前必须先确认库中不存在产出了多个产物的任务**，否则应先手工导出产物行。

## Open Questions

三条 Open Question 中，两条已由 8.1 的真实上游实测确认，GIF 一项有意搁置（不影响本次结构）。结论见「实测回填」，此处保留问题原文与各自的结论。

- **GIF 的索引透明是否被上游接受为透明通道的输入** —— **未验证（有意搁置）**：上游文档未把 GIF 列入该能力的可接受输入格式。8.1 未覆盖这一项。它不影响清单结构：`has_alpha` 无论对错都只是前端置灰的依据，最终由上游裁决；若将来确认 GIF 的索引透明被接受，改 `image_validation` 的判定并补测试即可，属局部改动。
- **`response_format: b64_json` 在 `/api/plan/v3` 上是否可用** —— **可用**：实测该取值被接受并按 b64 返回。因此取值域 `["url", "b64_json"]` 保持不变，不需要收窄；官方 skill 文档中「该路径固定 `url`」的说法在 API 层面不成立（skill 层与 API 层不同）。
- **`optimize_prompt_options` 是否还有其他档位、默认档位是哪个** —— **默认档位是 `standard`**，与种子所填一致，无需改数据；「是否还有其他档位」本次未继续穷举，取值域保持 `["standard", "fast"]`。

## 实测回填（8.1，2026-09-17）

对 `https://ark.cn-beijing.volces.com/api/plan/v3` 实测，结论如下（种子与上文已按此回填）：

| 实测项 | 结论 | 回填动作 |
|---|---|---|
| `layer_decomposition` 是否被接受 | 被接受 | 种子来源标记 `inferred` → `measured` |
| `background` 是否被接受 | 被接受 | 种子来源标记 `inferred` → `measured` |
| 种子里的 `model_id` 是否可用 | 可用（实测请求即以该标识发出并被接受） | 备注改为写明已实测，保留「以实际入口为准」的提示 |
| `optimize_prompt_options.mode` 默认档位 | `standard`（与种子一致） | 无需改数据；解除对应风险 |
| `response_format: b64_json` | 可用 | 取值域保持 `["url", "b64_json"]`；解除对应 Open Question |
| lite 的参考图上限 14 | **未单独复核**（种子维持 14，与上游文档一致） | 不改数据；该项与 GIF 同属未验证，不影响结构 |
| GIF 的索引透明 | 未验证 | 记在 Open Questions，不影响本次结构 |

副作用改动：种子改了来源标记后，`backend/tests/image_workbench/test_seed_and_menu.py` 中原本断言 `inferred` 的用例（`test_seeded_capabilities_mark_unmeasured_values_as_inferred`）随之改名为 `test_seeded_capabilities_mark_measured_values_as_measured` 并断言 `measured`——规格要求「未实测的取值得以辨认」是条件性的，不因此失效。