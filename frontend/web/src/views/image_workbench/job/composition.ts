/**
 * 绘图工作台 - 画布合成的**规则**（design D2/D10）。
 *
 * 合成规则集中在 `planComposition` 这一个函数里：上游若推翻了「底图铺底 + 图层按层级叠放」
 * 这条规则（它尚未在 `/api/plan/v3` 上实测），改动仅限此处，不触及提交契约、数据模型与规格。
 *
 * 与渲染分开是为了让规则可被直测——画布的像素输出依赖 DOM，而「哪一层先画、画在哪、缩放
 * 成多大」是纯数据，本文件只做后者。
 */
import type { JobArtifact } from "@/api/module_image_workbench/job";

/** 位置框的绝对像素表示 `[x1, y1, x2, y2]`（相对底图） */
export type AbsoluteBox = [number, number, number, number];

/** 一个待摆放的图层：产物 + 它的位置框（缺一不可，没有框就无从摆放） */
export interface CompositionLayer {
  artifact: JobArtifact;
  box: AbsoluteBox;
}

export interface CompositionPlan {
  /** 底图（`z_index` 为空的那一张）；没有则为 null */
  base: JobArtifact | null;
  /** 各图层，**已按 `z_index` 升序**——数组顺序即叠放顺序 */
  layers: CompositionLayer[];
  /** 没有底图时的画布尺寸：各图层位置框的并集（有底图时以底图的像素为准） */
  fallbackWidth: number;
  fallbackHeight: number;
}

/**
 * 位置框 → 绝对像素表示；缺失或形态不对时返回 null。
 *
 * 「形态不对就不画」而不是拿一个默认框顶上：位置框是上游给的唯一摆法，本地补不出来，
 * 硬摆到 (0,0) 会造出一张与产物列表不符的画面。
 */
export function absoluteBox(artifact: JobArtifact): AbsoluteBox | null {
  const raw = artifact.bounding_box?.absolute;
  if (!Array.isArray(raw) || raw.length !== 4) return null;
  const values = raw.map((item) => Number(item));
  if (values.some((item) => !Number.isFinite(item))) return null;
  return values as AbsoluteBox;
}

/**
 * 排出叠放计划：底图铺底，各图层按 `z_index` 升序（design D2）。
 *
 * **不做**「底图在最底所以可以短路」这类优化：上游把背景本身也可能拆成图层，一旦某层是
 * 整画布不透明的，只有严格的层级顺序是对的。层级相同时保持声明顺序（稳定排序），
 * 让同一次输入的渲染结果可复现。
 */
export function planComposition(artifacts: JobArtifact[] | undefined): CompositionPlan {
  const list = artifacts ?? [];
  const base = list.find((item) => item.is_base) ?? null;
  const layers = list
    .filter((item) => !item.is_base)
    .map((artifact) => ({ artifact, box: absoluteBox(artifact) }))
    .filter((item): item is CompositionLayer => item.box !== null)
    .sort((left, right) => (left.artifact.z_index ?? 0) - (right.artifact.z_index ?? 0));

  return {
    base,
    layers,
    fallbackWidth: layers.length ? Math.max(...layers.map((item) => item.box[2])) : 0,
    fallbackHeight: layers.length ? Math.max(...layers.map((item) => item.box[3])) : 0,
  };
}
