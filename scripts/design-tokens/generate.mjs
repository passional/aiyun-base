#!/usr/bin/env node
/**
 * 从 design/tokens.json 生成三端 token 文件。
 * 运行：node scripts/design-tokens/generate.mjs
 */
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";

const root = resolve(import.meta.dirname, "../..");
const tokens = JSON.parse(readFileSync(resolve(root, "design/tokens.json"), "utf8"));
const { brand, brandRamp, neutral, radius, space, font, fontFamily, shadow, control, table, layout, component } = tokens;

function write(path, content) {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, content, "utf8");
  console.log("generated", path.replace(root + "/", ""));
}

const radiusVars = Object.entries(radius)
  .map(([k, v]) => `  --fa-radius-${k}: ${v}px;`)
  .join("\n");
const spaceVars = Object.entries(space)
  .map(([k, v]) => `  --fa-space-${k}: ${v}px;`)
  .join("\n");
const fontVars = Object.entries(font)
  .map(([k, v]) => `  --fa-font-${k}: ${v}px;`)
  .join("\n");
const controlVars = Object.entries(control)
  .map(([k, v]) => `  --fa-control-h-${k}: ${v}px;`)
  .join("\n");

// ---- Web ----
write(
  resolve(root, "frontend/web/src/styles/core/_brand-tokens.generated.scss"),
  `// 由 design/tokens.json 生成，请勿手改。
:root {
  --fa-brand-blue: ${brand.blue};
  --fa-brand-violet: ${brand.violet};
  --fa-brand-gradient: ${brand.gradient};
  --fa-canvas: ${neutral.canvas};
${radiusVars}
${spaceVars}
${fontVars}
${controlVars}
  --fa-table-row-h: ${table.rowHeight}px;
  --fa-table-header-h: ${table.headerHeight}px;
  --fa-page-padding: ${layout.pagePadding}px;
  --fa-header-h: ${layout.headerHeight}px;
  --fa-shadow-card: ${shadow.card};
  --fa-shadow-pop: ${shadow.pop};
  --fa-shadow-modal: ${shadow.modal};
  --fa-font-sans: ${fontFamily.sans};
  --fa-table-header-bg: ${table.headerBgLight};
  --fa-input-bg: ${component.inputBgLight};
  --fa-dialog-bg: ${component.dialogBgLight};
  --fa-popover-bg: ${component.popoverBgLight};
  --fa-overlay-bg: ${component.overlayLight};
  --el-fill-color-blank: var(--fa-input-bg);
  --el-dialog-bg-color: var(--fa-dialog-bg);
  --el-bg-color-overlay: var(--fa-popover-bg);
  --el-overlay-color-lighter: var(--fa-overlay-bg);
}

html.dark {
  --fa-canvas: ${neutral.canvasDark};
  --fa-surface: ${neutral.darkSurface1};
  --fa-surface-2: ${neutral.darkSurface2};
  --fa-border: ${neutral.darkBorder};
  --fa-text-1: ${neutral.darkText1};
  --fa-text-2: ${neutral.darkText2};
  --fa-text-3: ${neutral.darkText3};
  --fa-shadow-card: ${shadow.darkCard};
  --fa-shadow-pop: ${shadow.darkPop};
  --fa-shadow-modal: ${shadow.darkModal};
  --fa-table-header-bg: ${table.headerBgDark};
  --fa-input-bg: ${component.inputBgDark};
  --fa-dialog-bg: ${component.dialogBgDark};
  --fa-popover-bg: ${component.popoverBgDark};
  --fa-overlay-bg: ${component.overlayDark};
}
`
);

// ---- Docs ----
write(
  resolve(root, "frontend/docs/.vitepress/theme/styles/brand-tokens.generated.css"),
  `/* 由 design/tokens.json 生成，请勿手改。 */
:root {
  --fa-brand-blue: ${brand.blue};
  --fa-brand-violet: ${brand.violet};
  --fa-brand-gradient: ${brand.gradient};
  --vp-brand-gradient: ${brand.gradient};
  --fa-border: ${neutral.border};
  --fa-shadow-card: ${shadow.card};
}

.dark {
  --vp-c-bg: ${neutral.darkSurface1};
  --vp-c-bg-alt: ${neutral.darkSurface2};
  --vp-c-bg-soft: ${neutral.darkSurface2};
  --vp-c-divider: ${neutral.darkBorder};
  --vp-c-border: ${neutral.darkBorder};
  --vp-c-text-1: ${neutral.darkText1};
  --vp-c-text-2: ${neutral.darkText2};
  --vp-c-text-3: ${neutral.darkText3};
}
`
);

// ---- App（CSS 变量，App.vue 引入一次）----
write(
  resolve(root, "frontend/app/src/styles/brand-tokens.generated.scss"),
  `/* 由 design/tokens.json 生成，请勿手改。 */
page {
  --fa-canvas: ${neutral.canvas};
  --fa-brand-blue: ${brand.blue};
  --fa-brand-violet: ${brand.violet};
  --fa-border: ${neutral.border};
  --fa-radius-sm: ${radius.sm}px;
  --fa-radius-md: ${radius.md}px;
  --fa-radius-lg: ${radius.lg}px;
  --fa-ink: ${neutral.ink};
  --fa-text-secondary: ${neutral.textSecondary};
  --fa-control-h-sm: ${control.sm}px;
  --fa-control-h-md: ${control.md}px;
  --fa-control-h-lg: ${control.lg}px;
  --fa-table-row-h: ${table.rowHeight}px;
  --fa-page-padding: ${layout.pagePadding}px;
  --fa-shadow-card: ${shadow.card};
  --fa-shadow-pop: ${shadow.pop};
}

/* 品牌色映射到 wot-ui 主色阶（浅/深主题都生效） */
page.wot-theme-light,
page.wot-theme-dark {
${Object.entries(brandRamp).map(([k,v]) => `  --wot-primary-${k}: ${v};`).join("\n")}
}
`
);

// ---- App dark（wot 主题变量覆写，单独引入一次）----
write(
  resolve(root, "frontend/app/src/styles/brand-dark.generated.scss"),
  `/* 由 design/tokens.json 生成，请勿手改。 */
.wot-theme-dark {
  --wot-filled-content: ${neutral.darkSurface1};
  --wot-filled-bottom: #101216;
  --wot-border-main: ${neutral.darkBorder};
  --wot-border-light: #20242C;
  --wot-text-main: ${neutral.darkText1};
  --wot-text-secondary: ${neutral.darkText2};
  --wot-text-auxiliary: ${neutral.darkText3};
  --wot-icon-main: ${neutral.darkText1};
  --wot-icon-secondary: ${neutral.darkText2};
  --wot-primary-5: ${brand.blue};
  --wot-primary-6: ${brand.blue};
}
`
);

console.log("done");
