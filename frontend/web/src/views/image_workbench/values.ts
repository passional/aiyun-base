/**
 * 绘图工作台共用的取值与地址辅助：只写这一处，各控件与页面共用。
 *
 * 取值是**上游要收的原样值**，因此界面上如实呈现（布尔显示 `true` / `false` 而不是
 * 「是 / 否」，数字不加千分位），避免管理员看到的与实际上行的不是同一个东西。
 */
import type { ParamControl } from "@/api/module_image_workbench/model";

/**
 * 存储节点内的相对路径 → 可直接访问的地址。
 *
 * 路径形如 `static/upload/file/2026/09/16/x.png`，是相对后端工作目录的；静态资源的公开
 * 地址是 `{ROOT_PATH}{STATIC_URL}` = `/api/v1/static` 再加上相对 static 的那一段（即去掉
 * 开头的 `static/`）。前端经既有的 `/api/v1` 代理即可直接 `<img>`。
 */
export function staticUrl(filePath: string): string {
  const relative = filePath.replace(/^\/+/, "").replace(/^static\//, "");
  return `${import.meta.env.VITE_APP_BASE_API}/static/${relative}`;
}

/** 取值 → 界面短文案（`null` / 空值呈现为空串，由占位符接手） */
export function formatValue(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

/**
 * 标签输入框的字符串标签 → 按 control 归一后的取值数组。
 *
 * 取值域里的数字与布尔需要还原成对应类型：`ElInputTag` 只会给字符串，直接落库会让
 * 后端把 `1` 与 `"1"` 判成两个不同的取值（`_value_key` 刻意不互认）。
 */
export function parseTagValues(
  tags: string[] | null | undefined,
  control: ParamControl
): unknown[] {
  const list = (tags ?? []).map((item) => String(item).trim()).filter(Boolean);
  if (control === "number") {
    return list.map((item) => Number(item)).filter((item) => Number.isFinite(item));
  }
  if (control === "switch") {
    return list
      .map((item) => {
        if (["true", "是", "1"].includes(item)) return true;
        if (["false", "否", "0"].includes(item)) return false;
        return undefined;
      })
      .filter((item) => item !== undefined);
  }
  return list;
}

/** 取值 → 标签输入框的字符串标签（单个值也呈现为一个标签，不静默吞掉） */
export function toTagValues(values: unknown): string[] {
  if (Array.isArray(values)) return values.map((item) => formatValue(item));
  if (values === null || values === undefined) return [];
  return [formatValue(values)];
}
