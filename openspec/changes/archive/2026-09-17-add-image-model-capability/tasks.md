# Tasks: 绘图模型能力声明与图层拆分（image-model-capability）

实现前阅读：仓库根 `SKILL.md`、`CONTEXT.md`、`docs/adr/0006`、`docs/adr/0007`、`docs/adr/0008`、`proposal.md`、`design.md`、两份 delta spec。

design.md 的三条 Open Question（GIF 索引透明、`b64_json` 是否可用、提示词优化的默认档位）都不改变**要建什么**——它们只影响清单里的**数据取值**，因此不阻塞本任务拆分，统一由 8.1 的实测回填。

## 1. 后端：能力声明的新结构

- [x] 1.1 按 design D1/D2 定义清单的 Pydantic 结构（参数条目、`modes`、`when` 的封闭词表、`reference` 块、`source` 来源标记），整体替换 `ImageWorkbenchCapabilitySchema`；验证：pytest 覆盖「默认值不在取值域内被拒」「取值重复被拒」「`when` 引用了不存在的参数键被拒」「`modes[].trigger` 指向不存在的参数被拒」四条断言
- [x] 1.2 实现清单的**求值器**：输入「清单 + 已填参数 + 本次请求事实」，输出每个参数的可用性与有效取值域/默认值（含按出图方式的 `overrides` 覆盖）；验证：pytest 覆盖「`when` 不满足 → 不可用」「`when` 满足 → 可用」「出图方式覆盖生效」「前端不认识的请求事实按不满足处理」四类断言
- [x] 1.3 实现清单驱动的参数校验与默认值补齐：用户显式提交的参数中，未知键、超出取值域、当前条件下不可用的三类被拒；未提交的参数**只在当前条件下可用时**才补默认值（design D5 的关键约束）；验证：pytest 覆盖上述四条，断言补齐后拿到的是一份完整参数集，并**专门覆盖「无参考图且未提交 background 的普通请求能正常通过」**这一条——它是「先补齐再校验」写法会堵死整条路径的直接反例

## 2. 后端：请求体组装与提交契约

- [x] 2.1 把 `build_ark_payload` / `build_openai_payload` 改为清单驱动：以 `model`/`prompt` 为基座，按参数键的点号路径逐层建字典（`optimize_prompt_options.mode` → 嵌套对象），参考图仍按接入方式走既有两条路径；删除 `_merge_extra_params` 与 `_RESPONSE_FORMAT` 常量；**参数的发与不发只由清单决定，不再由接入方式分支决定**——openai 兼容的模型在清单里不声明 `output_format` 与 `watermark` 条目，因此它们自然不会被发送；验证：pytest 断言嵌套字段形态正确、断言全仓不再存在硬编码的 `response_format` 常量、断言 payload **只**由清单参数构成（用一个未声明这两个参数的 openai 模型断言它们不在请求体里），并断言快照与实际 payload 的键集一致
- [x] 2.2 提交接口改为 `{model_pk, prompt, params, reference_images}`，服务端按 1.3 校验并补齐后落 `params_json` 快照；验证：pytest 断言快照**等于**实际发给上游的参数集（含补齐的默认值）
- [x] 2.3 图层拆分的前置校验：启用该出图方式时参考图必须恰好一张，否则拒绝提交；验证：pytest 覆盖 0 张、1 张、2 张三种输入
- [x] 2.4 参考图上传响应增加 `has_alpha`（design D9 的头部解析：PNG 的 IHDR colour type 4/6 与调色板 `tRNS`、WebP 的 `VP8X`/`VP8L` alpha 位，JPEG 与 GIF 判为无）；验证：pytest 用构造的字节序列覆盖 RGBA PNG、调色板 + `tRNS` PNG、无 alpha 的 PNG、JPEG、WebP 五类，并断言未引入新的图像处理依赖

## 3. 后端：产物模型与迁移

- [x] 3.1 新增 `image_workbench_artifact` 模型（design D7：`z_index` 可空且底图为空、位置框、名称、描述、文件路径、尺寸、格式）；验证：`uv run main.py revision -m "add image workbench artifact"` 生成迁移后 upgrade 与 downgrade 往返无报错
- [x] 3.2 迁移中把既有 `image_path`/`image_size` 转成产物行并删除任务表这两列（design Migration Plan A）；验证：在含单产物成功任务的库上 upgrade 后产物行数与成功任务数一致、`file_path` 与旧值相同、文件未被移动；downgrade 后两列回填且与旧值一致
- [x] 3.3 迁移中把 `capability_json` 的旧结构转成新结构（design Migration Plan A 第 4 步，含 `extra_params` 非空时的警告日志）；验证：在含旧结构模型行的库上 upgrade 后每行都能被新 schema 解析，且 `extra_params` 非空的用例产生一条警告日志
- [x] 3.4 产物表的 model 层 pytest（默认值、外键关系、取底图即 `z_index IS NULL`）；验证：`cd backend && uv run pytest` 通过

## 4. 后端：多产物执行与访问

- [x] 4.1 上游响应解析补齐图层字段（`z_index`、`name`、`description`、位置框的绝对与归一化两种表示）；验证：pytest 用官方文档给出的图层拆分响应样例断言全部字段被解析，且底图那条不带位置框
- [x] 4.2 产物转存改为多产物：逐张下载并写入 `image_workbench/{yyyy}/{mm}/{job_uuid}/`（底图 `base.{ext}`、图层 `layer_{z:02d}.png`），全部成功才把任务置为成功；任何一张失败即整单失败且不留部分产物；验证：pytest 覆盖全成功、中途写入失败、上游只回 b64 三条路径，并断言失败任务无产物行、无残留文件
- [x] 4.3 列表与详情的产物化，并新增按产物的下载端点、移除任务级下载端点（design D11）；验证：pytest 断言详情含 `artifacts[]` 且按层级有序、列表用底图作缩略、按产物下载返回对应文件、任务级下载端点已不存在
- [x] 4.4 删除任务时逐张真删全部产物文件并移除产物行；验证：pytest 断言多产物任务删除后存储中无残留文件、产物行全部消失，且进行中任务仍被拒绝删除
- [x] 4.5 用量记录与重试适配多产物：`usage_json` 的生成张数按实际产物数；**重试把原任务的参数快照作为「显式参数」重新提交**，因此走的是与首次提交完全相同的一套校验与补齐规则（1.3），不做任何绕过；验证：pytest 断言多产物任务的用量张数与产物行数一致、重试产生的新任务参数与原任务一致，并断言重试经过同一套校验（构造一个清单已改动、原快照含已不存在参数的情形，断言重试被拒而不是静默把旧参数发给上游）

## 5. 后端：种子改写

- [x] 5.1 按 design D10 重写 `backend/sql/image_workbench_model.json`：新结构、lite 的参考图上限改为 14、逐项来源标记（`layer_decomposition` 与 `background` 标 `inferred`）、`watermark` 默认 false 并在备注标明上游默认 true；验证：全新库初始化后两行都能被新 schema 解析，且 lite 的 `reference.max_images` 为 14
- [x] 5.2 确认 `initialize.py` 的两张表导入与 `service_id` 解析在新结构下仍工作；验证：全新库初始化后 `cd backend && uv run pytest` 通过，模型行挂到正确的 `service_id`

## 6. 前端：配置页

- [x] 6.1 按新结构改写能力声明的编辑表单（参数条目的增删、`control` 选择、取值域、默认值、`when`、按出图方式的覆盖、来源标记），`defineOptions({ name: 'ImageWorkbenchConfig' })` 与 `route_name` 保持一致；验证：`pnpm ts:check && pnpm lint && pnpm test` 通过；保存后重新打开原样回显
- [x] 6.2 同步 `api/module_image_workbench/` 的类型与契约（清单新结构、提交契约、产物、`has_alpha`）；验证：`pnpm ts:check` 通过，且无残留的旧字段类型
- [x] 6.3 补 i18n 词条到 `locales/langs/zh.json` 与 `en.json` 两份；验证：`pnpm lint` 通过，英文模式下无未翻译文案

## 7. 前端：工作台

- [x] 7.1 用清单驱动的通用表单替换手写控件：按参数条目逐条渲染（select / multiselect / switch / number / text），按求值结果置灰当前不可用的参数并说明原因，切换任一参数后重新求值；验证：`pnpm ts:check && pnpm lint && pnpm test` 通过；切换到 lite 时尺寸选项变化、选择 JPEG 时透明通道置灰、切到图层拆分后尺寸出现 `auto` 且默认值随之改变
- [x] 7.2 参考图上传后按 `has_alpha` 决定透明通道是否可用；验证：上传无透明通道的 JPEG 后该选项不可用，上传带透明通道的 PNG 后变为可用
- [x] 7.3 提交契约改为 `params` 字典（只提交用户改动过的键）；验证：提交后任务详情的参数快照包含全部参数，含服务端补齐的默认值
- [x] 7.4 任务详情与列表的产物化：多产物逐张呈现、按层级有序、每张可单独下载；列表仍用底图作缩略；验证：提交一次图层拆分后详情中可逐张查看与下载
- [x] 7.5 补 i18n 词条到两份 `locales/langs/*.json`；验证：`pnpm lint` 通过，英文模式下无未翻译文案

## 8. 端到端验收（由用户执行，实现方协助）

**本节任务不由实现方自行跑完就算数。** 真实上游验收由用户执行；实现方负责准备可复现的步骤与记录模板、在用户执行时协助定位问题，并在用户给出结论后把结论回填进仓库（种子、design.md）。每条任务以**用户确认**为完成标准。

- [x] 8.1 真实上游参数面确认（用户执行）：对 `https://ark.cn-beijing.volces.com/api/plan/v3` 实测 `layer_decomposition` 与 `background` 是否被接受、`optimize_prompt_options` 的默认档位、`response_format: b64_json` 是否可用、lite 的参考图上限；实现方准备可复现的请求与记录模板并负责回填；验证：用户确认结论，且种子里不再有与实际不符的 `inferred` 条目、design.md 的 Open Questions 逐条有结论
      - 用户确认结论：`layer_decomposition` 与 `background` **都被接受**；`optimize_prompt_options` 默认档位**确认为 `standard`**；`response_format: b64_json` **可用**；种子里的 `model_id` 经该实测确认可用。
      - 未验证两项（如实记录，均不影响清单结构）：GIF 的索引透明是否有用**有意搁置**；lite 的参考图上限 14 **未单独复核**（种子维持 14，与上游文档一致）。
      - 回填：种子中 `layer_decomposition` / `background` 的来源标记 `inferred` → `measured`，两行备注改写；`design.md` 新增「实测回填（8.1）」小节、Open Questions 逐条附结论、D10 与两条风险同步更新；随之把 `test_seed_and_menu.py` 中断言 `inferred` 的用例改为断言 `measured`。
- [x] 8.2 多产物全链路验收（用户执行）：完成一次图层拆分，确认底图与全部图层逐张可下载、上游地址失效后仍可访问、删除任务后文件全部消失；实现方准备步骤并协助定位；验证：用户确认
- [x] 8.3 参数面验收（用户执行）：确认水印开关两种状态产出的图片确实不同，且用户未改动时按清单默认值**不加水印**（注意上游默认是加水印）；验证：用户确认
- [x] 8.4 全量命令与构建验收（用户执行）：`cd backend && uv run pytest`；`cd frontend/web && pnpm ts:check && pnpm lint && pnpm test`；本地开发验证通过后，再按 `SKILL.md` 第 9 节重建并同步三份 dist 产物（`pnpm build:prod` + 带 `--delete` 的 rsync 到 `docker/nginx/web/dist` 与 `backend/dist`）；验证：用户确认命令全绿且三份 dist 已同步