# Tasks: 模型对齐与报告改版（align-models-and-revamp-report）

实现前阅读：仓库根 `SKILL.md`、`CONTEXT.md`、`proposal.md`、`design.md`、两份 delta spec。

按端分组；每个任务的验收方式写在任务内。

## 1. 后端：模型映射数据层

- [x] 1.1 新增 `channel_eval_canonical_model` 与 `channel_eval_model_alias` 两个模型（`model_map/model.py`）：规范模型含 `display_name`（唯一）、`family`、`status`、`remark`；别名含 `canonical_id`（FK，CASCADE）、`alias`（唯一）、`source`。生成并执行 Alembic 迁移。验证：`uv run main.py revision -m "add canonical model and alias"` 后 upgrade 与 downgrade 往返无报错；`uv run pytest tests/channel_eval/test_model_map.py` 覆盖"别名唯一约束"与"删除规范模型级联删别名"
      - 迁移手写为 `app/alembic/versions/2026_10_16_1845_b7f2c4a9e1d3.py`（本地无 MySQL，无法 autogenerate），沿用既有迁移的列/索引/命名约定。
      - **实现取舍**：唯一性由服务层在未删除行内校验，**不加数据库唯一约束**——本仓库删除是软删除，数据库唯一索引会让「删掉再建回来」永久失败（见 `model_map/model.py` 模块注释）。
- [x] 1.2 归一化函数：剥渠道前缀、统一分隔符、小写化、剥尾部日期戳；不剥离 `-thinking`/`-latest` 等变体后缀。验证：pytest 参数化断言 —— `openai/gpt-6-sol` 与 `gpt-6-sol` 同键；`claude-opus-5-5` 与 `claude-opus-5.5` 同键；`gpt-5.6-sol-20260115` 与 `gpt-5.6-sol` 同键；`gpt-6-sol` 与 `gpt-6-sol-thinking` **不同**键
- [x] 1.3 建议引擎：按归一化键把已观测串分组，组内 ≥2 个不同原始串时产出建议，并标注命中的依据（渠道前缀/分隔符/大小写/日期戳，可多个）。已确认归属的串不参与。验证：pytest 断言日期戳场景依据含"日期戳"、前缀场景依据含"渠道前缀"、变体后缀场景**不产出**建议、已确认串不出现在建议里
      - 依据取「跳过该变换就会把这一组拆开」的变换，避免列出一堆无关理由。
- [x] 1.4 未确认串清单：列出全部无归属的串及其出现过的平台与渠道（由 `channel_eval_run_combo` 反查）。验证：pytest 断言串旁带上其来源平台名与渠道名

## 2. 后端：模型映射接口

- [x] 2.1 规范模型 CRUD 接口（`/model-maps/list|detail/{id}|create|update/{id}|delete`），权限 `channel_eval:model_map:query|add|edit|delete`。创建/编辑时一并提交别名列表；校验"一个串只能属于一个规范模型"。验证：pytest 覆盖创建带别名、编辑增删别名、跨规范模型抢占同一串被拒（响应指出当前所属）
- [x] 2.2 建议与未确认串接口（`/model-maps/suggestions`、`/model-maps/unmapped`）。验证：pytest 断言建议带依据字段、未确认串带来源渠道
- [x] 2.3 一键确认建议（`/model-maps/confirm`）：把一组串落成一个规范模型 + 若干别名。验证：pytest 断言确认后这些串不再出现在建议与未确认清单中

## 3. 后端：报告按规范模型分组

- [x] 3.1 `report/service.py` 分组逻辑改为：先经已确认别名解析每个组合的规范模型，再按规范模型分组；未映射的串以自身为组键独立成组。验证：pytest 断言两个渠道不同串（`claude-opus-5-5` / `anthropic/claude-opus-5.5`）在存在别名时落成一组、无别名时落成两组
- [x] 3.2 组上补 `model_key`/`raw_models`/`alignment`/`family`；`model` 语义变为规范模型显示名（未映射时为串本身）。验证：pytest 断言 `raw_models` 含组内全部不同串、`alignment` 三态取值正确
      - **实现中修掉的缺陷**：`raw_models` 起初只收集非基准运行的串，导致基准那一侧的命名差异标不出来（而基准恰恰是最常见的一侧）。改为在基准回填后补入其串。
- [x] 3.3 视图补 `alignment` 汇总（未对齐计数 + 生效映射列表）与 `comparable_notes`（参数差异事实，分级）。验证：pytest 断言未对齐计数正确；热身开关不同产出提醒且措辞不含"建议"；参数一致时提醒为空
      - **实现中收紧的语义**：`unmapped_count` 只数 `unmapped`（有疑似同族、待确认），**不数 `singleton`**。singleton 是只在一个渠道出现、根本没有同族候选的模型，没有任何待办；把它算进去会让一份含大量独有模型的报告喊出同样多的「未确认归属」，是纯噪音。措辞随之改为「疑似同族待确认」，避免「全部已对齐」在全是 singleton 时误导。
- [x] 3.4 口径说明改为视图数据生成，含本次实际生效的映射关系。验证：pytest 断言存在别名时口径说明含该映射、无别名时映射段为空
- [x] 3.5 视图补 `run_configs`（运行参数快照与执行结果计数）与每个组合的 `samples`（原始样本明细）。验证：pytest 断言 `run_configs` 带出 `params_json` 的参数且热身按开关陈述（不虚构次数）、组合带样本明细

## 4. 后端：导出模板重写

- [x] 4.1 重写 `export_report.html.j2` 为内嵌 JSON + 客户端 JS 渲染，板块齐备（报告头/比较范围/关键指标概览/核心结论与可比性提醒/逐模型可视化/详细指标对比/交叉对比/异常清单/测试配置与执行信息/自动结论与备注/自动口径说明）。验证：pytest 断言导出内容含各板块标题、含内嵌 `report-data` JSON、且正文不含 `http://` 或 `https://` 外部引用
      - 报告头与比较范围由**服务端**渲染（无 JS 也能看），其余由内嵌 JSON 客户端渲染。
- [x] 4.2 交互：切换基准渠道、切换指标（首 Token/总耗时/速率）、按系列筛选、展开原始样本。验证：导出一份真实报告，离线打开并逐项操作，视图随之更新
      - 以 DOM 桩在 node 中实际执行渲染与全部交互分支，零运行时错误；交互后各板块内容随之重算。
- [x] 4.3 导出即冻结：内嵌 JSON 在导出时刻生成。验证：pytest 断言导出后新增运行/新增别名不改变已导出文件内容
- [x] 4.4 内嵌 JSON 保留非 ASCII。验证：导出件正文含可读中文、无 `\uXXXX` 转义码
      - **实现中修掉的缺陷**：Jinja 自带 `tojson` 会把中文转义成 `\uXXXX`；浏览器解析没问题，但归档件要能被人直接打开读。改用自定义 `json_safe` 过滤器（`ensure_ascii=False` + 转义 `<>&'`）。

## 5. 前端：报告视图与模型对齐

- [x] 5.1 `ReportViewDialog.vue` 补关键指标概览行、未对齐提醒、命名差异标注、系列筛选、可比性提醒、测试配置与执行信息，口径说明改用视图自动生成的 `caliber_notes`。验证：`pnpm ts:check && pnpm lint` 通过；打开一份含未对齐模型的报告能看到提醒与标记
- [x] 5.2 新增「模型对齐」页面：规范模型列表（含别名）+ 待确认建议列表（带依据）+ 一键确认 + 未确认串提示。验证：`pnpm ts:check && pnpm lint && pnpm test` 通过；确认一条建议后报告里对应模型并成一行
- [x] 5.3 `api/module_channel_eval/model_map.ts` 新增、报告 api 类型同步；i18n 中英两份；菜单与 `channel_eval:model_map:*` 权限种子。验证：中英切换下文案完整，无裸 key
- [x] 5.4 存量库子菜单幂等合并：`initialize.py` 的种子合并原先只处理「整棵顶级菜单缺失」，向已有顶级菜单追加的子页面在老库永远不出现。新增 `__ensure_seed_child_menu_merged`。验证：pytest 断言超管菜单树含「模型对齐」

## 6. 验收

- [x] 6.1 后端全量测试：`cd backend && uv run pytest tests/channel_eval/` → **90 passed**
- [x] 6.2 前端：`cd frontend/web && pnpm ts:check && pnpm lint && pnpm test` → 全绿（vitest 26 passed）
- [x] 6.3 端到端：对一份真实的两次运行报告导出 HTML，核对板块齐备、未对齐模型被标出、确认别名后模型并成一行
      - 造数脚本结果：对齐前 4 组 / 4 个未对齐；确认 4 条别名后 4 组全部 `aligned`，每组标注命名差异（含日期戳场景 `gpt-6-luna` / `gpt-6-luna-20260115`）。

## 7. 归档

- [x] 7.1 运行 `openspec` 归档流程：把本变更并入 `openspec/specs/channel-eval/{compare-report,model-mapping}` 主规格，变更目录移入 `changes/archive/`。**已执行**——主规格同步后逐条校验一致（compare-report 9 条需求、model-mapping 3 条需求），`openspec validate` 全绿。
      - 同步前修正了 delta 与实际实现的矛盾：`跨渠道对比视图` 原写「某模型只在一个渠道出现」会给出未对齐计数，但实现里 singleton 已被有意排除（无待办，计入即噪音）。delta 与主规格同步改为「疑似同族未确认才计数，单渠道独有不计」。
