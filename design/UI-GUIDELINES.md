# FastapiAdmin UI 规范（强制）

> 适用范围：`frontend/web` 全部页面、组件、弹窗、移动端与主题。
> 状态：生效中。**新增或修改 UI 必须遵守本规范；不符合的代码不应合并。**
> Token 唯一来源：`design/tokens.json`，生成命令 `node scripts/design-tokens/generate.mjs`。

---

## 0. 十条硬性规则（MUST）

1. **禁止分割线与拖拽手柄**。多栏用固定宽侧栏 + 16px 间距布局，不做可拖拽分隔条。
2. **禁止魔法高度**。不允许 `h-150`、`max-height: 52vh` 这类固定值决定一屏布局；用 `h-full + min-h-0 + flex-1`，内部滚动。
3. **卡片间距 16px**，页面内边距 16px；卡片圆角 12px。
4. **移动端必须卡片化**。`< 1025px` 的列表使用卡片或 `FaTable mobile-cards`，不允许横向挤压的表格。
5. **操作按钮行必须换行**，不得溢出卡片；统一 `mobile-card-actions`。
6. **破坏性操作只用危险色**：默认透明、红字/浅红描边，hover 淡红；禁止灰色背景。
7. **所有 UI 文案走 i18n**，禁止硬编码中文；业务数据（角色名、部门名等）除外。
8. **属性里不能用 `{{ }}`**，必须 `:placeholder="$t('...')"`、`:title="$t('...')"`。
9. **顶栏语言切换必须点击展开**，不允许 hover 触发。
10. **一屏布局必须内部滚动**，页面本身不出现第二条滚动条（移动端允许整页滚动）。

---

## 1. 设计 Token

| 组 | 规范 |
| --- | --- |
| 品牌色 | `#4F6BFF` → `#6D5BFF`（135deg） |
| 画布 | 浅色 `#F5F6F8`，深色 `#15181E` |
| 卡片 | 深色 `#191D24`，边框 `#262B34`；浅色白底 + `#E6E8EC` |
| 文字 | 主 `#E5E7EB` / 次 `#A3AAB8` / 弱 `#8B93A1`（深色） |
| 圆角 | 卡片 12，控件 8，小标签 6，胶囊 pill |
| 间距 | 4 / 8 / 12 / 16 / 20 / 24；**模块间距固定 16** |
| 字号 | 12 / 13 / 14 / 16 / 20 / 24 |
| 控件高 | 24 / 32 / 40 |
| 表格 | 行高 40，表头 44 |
| 布局 | 页面内边距 16，顶栏 56 |
| 断点 | 800 / 992 / 1280；列表卡片断点 **1025** |

禁止在新代码里写死十六进制颜色、圆角、间距；使用 `var(--el-*)`、`var(--fa-*)` 或 Tailwind token。

---

## 2. 页面结构

```html
<div class="fa-full-height">
  <FaPageHeader ... />
  <FaSearchBar ... />
  <FaTableHeader ... />
  <FaTable ... />
</div>
```

- 页面根节点必须 `fa-full-height`。
- 主内容卡片 `fa-card`，卡片之间 `gap-4`（16px）。
- 双栏工作台统一 `FaWorkbench`：左侧固定宽（280 / 320），右侧 `min-w-0 flex-1`；**不做 splitter**。
- 窄屏隐藏空白侧栏，必须同时隐藏父级占位槽，避免 320px 空白。
- 页签容器使用 `fa-tabs-fill`，内容区 `h-full min-h-0`。

### 2.1 一屏布局（必须）

- 高度链：`fa-full-height` → `fa-tabs-fill` → `el-tab-pane` → 内容 `h-full min-h-0`。
- 固定区 `shrink-0`（标题、工具栏、筛选）。
- 伸缩区 `flex-1 min-h-0`（表格、图表、卡片列表）。
- 内容超出只在伸缩区内部滚动。
- 图表容器必须有可测量高度（如 `min-h-75`），否则 ECharts 初始化空白。
- 参考实现：`views/module_monitor/cache/index.vue`。

---

## 3. 断点与响应式

| 宽度 | 列表形态 |
| --- | --- |
| `< 800px` | 单列移动卡片；工具栏换行；隐藏侧栏 |
| `800–1024px` | 双列卡片（`mobile-card-list` 自动 grid） |
| `1025–1200px` | 双列卡片，分页跨列 |
| `> 1200px` | 常规表格 |

规则：

- 列表卡片断点统一 `viewportWidth.value < 1025`。
- 卡片容器统一 `class="mobile-card-list mt-4 flex flex-col gap-4"`。
- 800–1200 的卡片 grid 由全局 `.mobile-card-list` 控制，不要在页面里重复写。
- 表格页窄屏用 `<FaTable mobile-cards />`，不要自己再写一套卡片。

---

## 4. 表格

- 必须用 `FaTable`，列配置走 `ColumnOption[]`。
- 时间列宽 **190px**，`formatDateTime` 输出到秒。
- 状态列用 `status` + `FaStatusTag`，标签高 26px、字号 12px。
- 操作列 `width: 180`、`fixed: "right"`、`align: "center"`，用 `renderTableOperationCell`。
- 工具栏：`FaTableHeader` + `FaTableHeaderLeft`；按钮自然宽度，`ElSpace` 必须允许换行。
- 移动端工具栏：容器和 `ElSpace` 强制 `width:100%` + `flex-wrap`，禁止 nowrap。
- 空数据：隐藏分页，显示 `FaEmptyState`；骨架屏仅首屏。
- 禁止在 1024px 以下保留挤压的多列表格。

### 4.1 通用移动卡片

```html
<FaTable mobile-cards ... />
```

- 标题列优先 `name / title / username / notice_title / request_path / version`。
- 其余列自动渲染为「标签：值」，最多 8 项。
- 时间/描述类字段跨两列。
- 支持全选/单选，`selection-change` 与桌面一致。
- 卡片模式限制：无行展开、无树形；复杂页面可保留手写卡片，但必须补批量选择。

---

## 5. 手写移动卡片

结构：

```html
<div class="p-4 rounded-lg bg-(--default-box-color) border border-(--default-border)">
  <div class="flex items-start justify-between gap-3">
    <ElCheckbox ... />
    <div class="min-w-0 flex-1">标题</div>
    <FaStatusTag ... />
  </div>
  <div class="grid grid-cols-2 gap-x-3 gap-y-1.5 mt-3 text-xs text-g-600">
    <div>字段：值</div>
  </div>
  <div class="mobile-card-actions mt-3 flex flex-wrap justify-end gap-1">操作</div>
</div>
```

强制：

- 操作行必须 `flex flex-wrap`，按钮 `flex:0 0 auto`，不得溢出。
- 批量选择：卡片左侧复选框 + 列表顶部「全选 / 半选」，复用 `selectedRows` 与 `onTableSelectionChange`。
- 树形卡片全选只选当前可见节点。
- 空态、字段标签、状态值全部 i18n。
- 卡片与工具栏、卡片与卡片间距 16px。

---

## 6. 弹窗与抽屉

- 统一 `FaDialog` / `FaDrawer`，不直接散写 `ElDialog`。
- 移动端宽度：`min(920px, calc(100vw - 24px))`，实际 338–351px。
- 底部按钮固定可见；内容区 `overflow-y-auto`。
- 表单弹窗标题、按钮、校验文案必须 i18n。
- 复杂设计器（工作流）在 `≤900px`：
  - 纵向堆叠；
  - 隐藏小地图与缩放控件；
  - 画布 `min-height: 420px`；
  - 配置面板宽度 100%。
- 详情弹窗用 `FaDescriptions`，在 375px 下不得横向溢出。

---

## 7. 颜色语义

| 语义 | 颜色 | 用法 |
| --- | --- | --- |
| 主操作 | Primary `#4F6BFF` | 新增、查询、保存、运行中 |
| 成功 | Success | 启用、成功、已连接 |
| 警告 | Warning | 超时、回滚、待处理风险 |
| 危险 | Danger | 删除、强退、退出登录、停用（仅破坏性） |
| 信息 | Info | 待处理、已取消、禁用 |

- **一个区域只允许一个主按钮（实心）**，其余用默认/文字按钮。
- 状态标签颜色必须与语义表一致，不得用 danger 表示普通状态。
- 破坏性按钮：默认透明 + 危险色文字/浅红边框；hover 淡红；**禁止灰色底**。
- 用户菜单「退出登录」已按此规范：`398ac093`。

---

## 8. 文案与 i18n

- 所有 UI 文案必须走 `$t("...")` / `t("...")`，zh/en 键必须同时存在。
- 不允许在模板属性里写 `title="{{ $t(...) }}"`；必须 `:title="$t('...')"`。
- 常见文案集中映射：
  - 列头/表单/占位符：`utils/table/columnLabelI18n.ts`
  - 状态：`translateStatusText`
  - 菜单标题：`formatMenuTitle`
  - 工具栏/搜索/上传导入导出：共享组件内部
- 业务数据（角色名、部门名、菜单标题、用户姓名）保持原文，不翻译。
- 语言切换必须 `trigger="click"`（`5ab83668`）。
- 新增页面必须验证中英文两种模式。

---

## 9. 表单与搜索

- 统一 `FaForm` / `FaSearchBar`，标签宽度一致，默认 `label-position="right"`。
- 占位符统一「请输入 xxx / 请选择 xxx」，走集中映射。
- 校验提示必须 i18n；必填星号在标签前。
- 搜索栏按钮顺序：重置 → 查询 → 展开/收起；移动端允许换行。
- 表单栅格：桌面 2 列，移动端 1 列（`span=24`）。
- 同一表单组内输入框、下拉框、日期选择器高度必须一致；移动端跟随 `ConfigProvider size="small"`，不要单独给 `ElSelect` 写死 40px。

---

## 10. 空态、加载、分页

- 空态：`FaEmptyState`，标题 + 一句说明；列表为空隐藏分页。
- 加载：首屏骨架屏，操作中用按钮 loading，禁止整页遮罩。
- 分页：`FaPagination`；移动端允许缩小，但不得盖住卡片。
- 卡片模式下分页独立于滚动区，卡片区底部预留空间。

---

## 11. 侧栏、顶栏与用户菜单

- 侧栏菜单标题统一 `formatMenuTitle`，支持 i18n。
- 工作页签自动滚动到激活项，右侧留 96px 避让箭头工具条。
- 顶栏语言切换：点击展开，显示语言列表与选中勾。
- 头像 hover 弹层：
  - 条目 hover 主色 10% 背景；
  - 分割线 1px；
  - 退出登录使用危险色规范样式，不置灰。

---

## 12. 代码生成模板

`backend/templates/vue/index.vue.jinja2` 必须与手写页一致：

- `FaTable mobile-cards`；
- 操作文案 `t("common.detail/edit/delete")`；
- 时间列宽 190；
- 工具栏、搜索、状态、表单 i18n 自动继承共享组件；
- 生成页移动端由通用卡片承担，不在模板复制卡片布局。

修改规范或共享组件后，必须同步检查生成模板。

---

## 13. 提交前检查清单

- [ ] 无分割线/拖拽手柄
- [ ] 无固定魔法高度；一屏布局内部滚动
- [ ] 卡片间距 16、圆角 12、页面内边距 16
- [ ] `< 1025px` 已卡片化，操作行不溢出
- [ ] 破坏性按钮危险色，无灰色背景
- [ ] 所有新增文案有 zh/en key，属性用绑定
- [ ] 语言切换点击展开
- [ ] 弹窗/抽屉在 375px 无横向溢出、底部按钮可见
- [ ] 空态、分页、骨架屏正常
- [ ] 深色/浅色主题均检查
- [ ] `pnpm ts:check`、`pnpm exec eslint`、`pnpm build` 通过
- [ ] 截图验证 375 / 768 / 1024 / 1440

---

## 14. 常用参考文件

| 内容 | 文件 |
| --- | --- |
| 设计 Token | `design/tokens.json` |
| 全局布局样式 | `frontend/web/src/styles/core/_app.scss` |
| 表格 | `frontend/web/src/components/tables/fa-table/index.vue` |
| 表格工具栏 | `frontend/web/src/components/tables/fa-table-header-left/index.vue` |
| 表单/搜索 i18n | `frontend/web/src/utils/table/columnLabelI18n.ts` |
| 菜单标题 i18n | `frontend/web/src/utils/navigation/index.ts` |
| 用户菜单 | `frontend/web/src/layouts/fa-header-bar/widgets/FaUserMenu.vue` |
| 缓存监控一屏布局 | `frontend/web/src/views/module_monitor/cache/index.vue` |
| 代码生成模板 | `backend/templates/vue/index.vue.jinja2` |

---

## 15. 违规示例（禁止）

```html
<!-- 禁止：固定高度决定一屏 -->
<div class="h-150">...</div>

<!-- 禁止：属性里写 mustache -->
<ElInput placeholder="{{ $t('common.search') }}" />

<!-- 禁止：破坏性操作灰色 hover -->
<li class="hover:bg-(--fa-gray-200)">退出登录</li>

<!-- 禁止：移动端操作行不换行 -->
<div class="flex justify-end gap-1">...</div>

<!-- 禁止：hover 展开语言 -->
<ElDropdown>...</ElDropdown>
```

正确写法见第 2–11 节。


---

## 16. 跨端品牌统一（Web / 官网 / 小程序）

### 16.1 Logo 与 favicon

- 唯一来源：`design/assets/`；
- 修改后执行：
  ```bash
  node scripts/sync-brand-assets.mjs
  ```
- 禁止在官网 `public`、小程序 `static`、Web `public` 单独替换 Logo。

### 16.2 品牌色

- 主色：`#4F6BFF`；
- 辅助色：`#6D5BFF`；
- 渐变：`135deg #4F6BFF → #6D5BFF`；
- 三端统一引用 `--fa-brand-blue` / `--fa-brand-violet` / `--fa-brand-gradient`，不再使用旧 indigo/pink 色值。

### 16.3 形状与间距

| Token | Web | 官网 | 小程序 |
| --- | --- | --- | --- |
| 卡片圆角 | 12px | 12px | 24rpx |
| 控件圆角 | 8px | 8px | 16rpx |
| 页面内边距 | 16px | 16px | 32rpx |
| 模块间距 | 16px | 16px | 32rpx |

### 16.4 表面与文字

- 亮色画布 `#F5F6F8`，卡片 `#FFFFFF`，边框 `#E6E8EC`；
- 深色画布 `#15181E`，卡片 `#191D24`，边框 `#262B34`；
- 标题、正文、辅助文字参考 `design/tokens.json`；
- 官网与小程序不得使用主题色浅阶作为卡片底色（登录卡片等以中性表面为准）。

### 16.5 按钮

- 高度：24 / 32 / 40；
- 圆角 8px（小程序 16rpx）；
- 一个区域只允许一个主按钮；
- 危险操作使用危险色规范，不用主题色。

### 16.6 验收

- 三端 Logo 一致；
- 主色与渐变一致；
- 卡片/控件圆角一致；
- 官网与小程序构建通过。

### 16.7 自动检查

```bash
node scripts/check-brand-consistency.mjs
```

提交前必须通过；CI 可将其作为门禁。
