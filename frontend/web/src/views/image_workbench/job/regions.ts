/**
 * 绘图工作台 - 区域色与区域占位符（纯函数，便于直测）。
 *
 * 两条规则：
 *
 * 1. **颜色按选区序号固定绑定**：`区域1` 永远是调色板第 1 色，删除别的选区不会让颜色洗牌；
 *    超过调色板长度后循环复用，序号文字仍是唯一标识。
 * 2. **占位符写法来自能力声明**（`marker.placeholder`，如 `区域{n}`），不得在代码里硬编码
 *    「区域」——声明换了写法，提示词高亮与画布标签仍然成立（design D4）。
 */

export const REGION_PALETTE_SIZE = 8;

/** 提示词中的一个片段：普通文字，或一个带序号的区域占位符 */
export interface PromptSegment {
  text: string;
  /** 占位符序号；普通文字为 null */
  index: number | null;
}

/** 序号 → 调色板位置（1..REGION_PALETTE_SIZE），非有限值按第 1 色处理 */
export function regionPaletteIndex(index: number): number {
  const normalized = Number.isFinite(index) ? Math.trunc(index) : 1;
  const zeroBased =
    (((normalized - 1) % REGION_PALETTE_SIZE) + REGION_PALETTE_SIZE) % REGION_PALETTE_SIZE;
  return zeroBased + 1;
}

/** 序号 → CSS 变量名（颜色定义在 design/tokens.json，深色模式另有一组） */
export function regionColorVar(index: number): string {
  return `--fa-region-${regionPaletteIndex(index)}`;
}

/** 序号 → 前景色（文字、描边、标签底色） */
export function regionColor(index: number): string {
  return `var(${regionColorVar(index)})`;
}

/** 序号 → 淡底色（占位符背景、标签背景），与前景色同源 */
export function regionSoftColor(index: number): string {
  return `color-mix(in srgb, ${regionColor(index)} 12%, transparent)`;
}

/** 序号 → ElTag 的颜色变量：保持 Element Plus 的浅色标签形态，只换颜色 */
export function regionTagStyle(index: number): Record<string, string> {
  return {
    "--el-tag-text-color": regionColor(index),
    "--el-tag-bg-color": regionSoftColor(index),
    "--el-tag-border-color": regionColor(index),
  };
}

/** 占位符模板 + 序号 → 文案（`区域{n}` → `区域1`）；模板不合法时退回既有写法 */
export function formatRegionLabel(template: string | null | undefined, index: number): string {
  const source = template && template.includes("{n}") ? template : "区域{n}";
  return source.replace("{n}", String(index));
}

/**
 * 占位符模板 → 匹配规则。
 *
 * 模板是数据（如 `区域{n}`），因此按 `{n}` 切开、转义字面部分、用 `(\d+)` 捕获序号；
 * 模板不含 `{n}` 时返回 null（无法从文本里认出序号，宁可不染色）。每次调用都新建
 * 正则，避免 `lastIndex` 在多次渲染之间残留。
 */
export function placeholderPattern(template: string | null | undefined): RegExp | null {
  if (!template || !template.includes("{n}")) return null;
  const escape = (value: string) => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return new RegExp(template.split("{n}").map(escape).join("(\\d+)"), "g");
}

/** 把提示词切成「普通文字 / 区域占位符」片段，供镜像层逐段上色 */
export function splitPromptSegments(
  prompt: string,
  template: string | null | undefined
): PromptSegment[] {
  const pattern = placeholderPattern(template);
  if (!pattern) return prompt ? [{ text: prompt, index: null }] : [];

  const segments: PromptSegment[] = [];
  let cursor = 0;
  for (const match of prompt.matchAll(pattern)) {
    const start = match.index ?? 0;
    if (start > cursor) segments.push({ text: prompt.slice(cursor, start), index: null });
    segments.push({ text: match[0], index: Number(match[1]) });
    cursor = start + match[0].length;
  }
  if (cursor < prompt.length) segments.push({ text: prompt.slice(cursor), index: null });
  return segments;
}

/** 画布需要真实颜色值（canvas 不认 `var(...)`），从根元素读当前主题下的 token */
export function resolveRegionColor(index: number): string {
  if (typeof document === "undefined") return "black";
  const root = getComputedStyle(document.documentElement);
  return (
    root.getPropertyValue(regionColorVar(index)).trim() ||
    root.getPropertyValue("--el-color-primary").trim() ||
    "black"
  );
}
