## Why

本仓库派生自上游 FastapiAdmin 模板，其自带的演示内容——编造的统计数据、框架营销文案、示例页面、上游作者外链与备案号——至今仍完整呈现在我们部署的管理台上。首页没有一个数字与渠道评估有关；登录页在挂载 500ms 后弹出一条**永不自动消失**的开源项目求 Star 通知；顶栏头像菜单里的 GitHub/Gitee 指向上游仓库；`config/modules/festival.ts` 里一条 `isResidentBanner: true` 的全年常驻公告正在**每个登录后页面顶部滚动**，并带一个跳去上游演示站的链接；登录页页脚显示的 `陕ICP备2025069493号-1` 是上游作者的备案号。

这不只是观感问题。备案号属于需要与实际运营主体一致的信息；「演示账号快速选择」在校验通过后会把 `super` / `123456` 预填进登录表单，任何人打开登录页即得到一组管理员凭证；页脚的源码地址、帮助、隐私、条款链接全部指向上游站点。管理台对外呈现的主体与内容必须是我们自己的。

选择现在处理，是因为清理面已经被完整测绘、且与渠道评估功能完全解耦——不存在「先等功能做完再清理」的依赖。

## What Changes

- **首页只呈现真实数据**：删除「总收入 ¥350,000」「任务进度 65%/80%」「浏览量 15480」「粉丝数 2545」「粉丝量 36358」「最近交易（John Doe $385.90）」「日程日历」「新用户」「待办事项（张无忌/赵敏）」「文章卡片」「IT 通知（含 `FastapiAdmin v3.1.0 已发布`）」「关于项目」共 12 组演示组件；保留三张有真实接口支撑的 KPI 卡片、系统健康、快速链接，并新增「平台数 / 运行数 / 最近运行」三块真实业务概览。
- **首页停止使用 mock 与静默回退**：移除 `getDashboardMock()` 及其种子数据；KPI 卡片接口失败时不得回退到写死的数字。
- **BREAKING** 删除 `/dashboard/workplace` 与 `/dashboard/analysis` 两个演示页面及其侧边栏菜单节点（两个页面无任何业务含义：前者是电商演示，后者是裸 ECharts 组件画廊）。
- **登录页去推广化**：删除求 Star 通知、演示账号快速选择（含 `super` 预填）、全假的扫码登录面板与手机号登录面板。
- **顶栏与全局去上游外链**：删除头像菜单的 GitHub/Gitee 两项；删除「快速入口」中的官方文档/技术支持两项；删除控制台 Star 横幅；删除 `WEB_LINKS` 中已成为死常量的上游链接。
- **常驻公告改为自有文案**：保留节日公告机制，将全年常驻条目的上游文案与上游链接替换为自有内容。
- **BREAKING** 移除第三方 OAuth 登录入口：四个 provider（微信/QQ/GitHub/Gitee）凭证全部为空、点击必然得到「未配置」报错，登录页不再展示该入口区块。
- **品牌默认值不再指向上游主体**：`sys_param.json` 中 `keep_record`（备案号）种子值改为空，使全新数据库启动时默认不展示任何备案号。其余品牌项按既定口径**不改种子**，由管理员在「参数管理」中维护（已确认参数管理保存的值不会被重新部署覆盖）。
- **修正开发机地址残留**：`OAUTH_FRONTEND_FALLBACK` 默认值 `http://127.0.0.1:5173/login` 改为从环境变量注入。
- **清理悬空文档**：`fastlink/tutorial` 中引用 workplace/analysis 的章节一并移除。
- **去模板化术语写入词汇表**：`CONTEXT.md` 收录 `上游模板` / `模板痕迹` / `业务内容` / `品牌配置`，作为后续同类清理的判据。

不在本次范围：`frontend/docs/`（VitePress 文档站）、`frontend/app/`（uni-app 移动端）、README、`package.json` 包名、代码注释中的上游署名。

## Capabilities

### New Capabilities

- `admin-console/dashboard`: 管理台首页只呈现来源真实的数据——每个统计块要么由真实接口驱动，要么不存在；不得以写死数字或静默回退填充。
- `admin-console/whitelabel`: 管理台对终端用户可见的界面不呈现上游模板痕迹——登录页、顶栏、全局横幅、品牌默认值均不含上游推广内容、上游外链与上游主体资质信息。

### Modified Capabilities

无。现有 `channel-eval/{platform-access,test-run,compare-report}` 三个 capability 的行为均不改变——本次清理不触碰任何渠道评估的数据流、接口契约或权限。

## Impact

**frontend-web**（主要影响面）
- 首页：`views/dashboard/home/index.vue` 与 `modules/`（删 6 个组件、改索引页、移动 `HealthItem` 类型）
- 路由与菜单：`router/routes.ts`、`router/MenuProcessor.ts`、`__tests__/route-invariants.spec.ts`（现有断言依赖 `Dashboard` 子路由数 > 0，必须同步修改）
- 登录页：`views/module_system/auth/login/`（`index.vue`、`components/forms/FaLoginAccountForm.vue`、`components/panels/`、`components/widgets/FaLoginThirdPartySection.vue`）
- 顶栏：`layouts/fa-header-bar/widgets/FaUserMenu.vue`
- 全局配置：`config/modules/fastEnter.ts`、`config/modules/festival.ts`、`utils/constants/definitions.ts`、`utils/sys/index.ts`
- 手册：`views/fastlink/tutorial/`
- i18n：`locales/langs/zh.json` 与 `en.json` 成对修改

**backend**
- `app/config/setting.py`：`OAUTH_FRONTEND_FALLBACK` 改为从环境变量注入
- `sql/sys_param.json`：仅 `keep_record` 一处种子值置空
- 无 model 变更，**无 Alembic 迁移**
- 种子导入是整表级跳过（`scripts/initialize.py:174-186`），改动仅对全新数据库生效，不影响既有库

**frontend-app / frontend-docs / docker**：无改动。

**兼容性**
- 移除两个演示页面与 OAuth 入口属**破坏性变更**：任何指向 `/dashboard/workplace`、`/dashboard/analysis` 的书签或外链将落入 404；依赖演示账号快速选择的开发流程需改为手工输入。
- `WEB_LINKS` 与 `sys_param` 的键名保持不变，仅移除已无引用的常量与调整一个种子值，不构成接口契约变更。