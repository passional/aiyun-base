/**
 * 画布合成规则的直测（任务 4.1 / design D2）。
 *
 * 「画面与产物列表一致」这条要求里，可被机器核的是**叠放顺序与摆放位置**：底图铺底、
 * 各图层按 `z_index` 升序、按其位置框缩放后摆到框的左上角。像素输出依赖 DOM，不在这里测。
 */
import { describe, expect, it } from "vitest";

import type { JobArtifact } from "@/api/module_image_workbench/job";
import { absoluteBox, planComposition } from "./composition";

function artifact(overrides: Partial<JobArtifact> & { id: number }): JobArtifact {
  return { z_index: null, ...overrides };
}

/** 底图：无层级、无位置框 */
function baseArtifact(id = 1): JobArtifact {
  return artifact({ id, is_base: true, z_index: null });
}

/** 图层：带层级与位置框 */
function layer(id: number, zIndex: number, box: number[] | null): JobArtifact {
  return artifact({
    id,
    is_base: false,
    z_index: zIndex,
    bounding_box: box ? { absolute: box, normalized: box } : null,
  });
}

describe("planComposition", () => {
  it("底图铺底：无论它在产物列表里排第几，都单独取出来先画", () => {
    const plan = planComposition([
      layer(2, 1, [0, 0, 10, 10]),
      baseArtifact(1),
      layer(3, 2, [0, 0, 10, 10]),
    ]);
    expect(plan.base?.id).toBe(1);
    expect(plan.layers.map((item) => item.artifact.id)).toEqual([2, 3]);
  });

  it("图层严格按 z_index 升序，不因输入顺序而改变叠放", () => {
    const scrambled = [
      layer(30, 3, [0, 0, 10, 10]),
      layer(10, 1, [0, 0, 10, 10]),
      layer(20, 2, [0, 0, 10, 10]),
    ];
    expect(planComposition(scrambled).layers.map((item) => item.artifact.z_index)).toEqual([
      1, 2, 3,
    ]);
  });

  it("层级相同时保持输入顺序（稳定排序，同一次输入渲染可复现）", () => {
    const plan = planComposition([layer(7, 1, [0, 0, 10, 10]), layer(8, 1, [0, 0, 10, 10])]);
    expect(plan.layers.map((item) => item.artifact.id)).toEqual([7, 8]);
  });

  it("没有位置框的图层不参与合成：摆不出来就不画，不硬塞到 (0,0)", () => {
    const plan = planComposition([baseArtifact(), layer(2, 1, [0, 0, 10, 10]), layer(3, 2, null)]);
    expect(plan.layers.map((item) => item.artifact.id)).toEqual([2]);
  });

  it("没有底图时给出各图层位置框的并集作为画布尺寸", () => {
    const plan = planComposition([layer(1, 1, [10, 20, 300, 400]), layer(2, 2, [0, 0, 500, 150])]);
    expect(plan.base).toBeNull();
    expect(plan.fallbackWidth).toBe(500);
    expect(plan.fallbackHeight).toBe(400);
  });

  it("空产物列表得到空计划（画布退化为显示参考图）", () => {
    for (const input of [undefined, []]) {
      const plan = planComposition(input);
      expect(plan.base).toBeNull();
      expect(plan.layers).toEqual([]);
      expect(plan.fallbackWidth).toBe(0);
    }
  });
});

describe("absoluteBox", () => {
  it("只认形态正确的四个数字：缺一个、多一个、非数字都不算数", () => {
    expect(absoluteBox(artifact({ id: 1, bounding_box: { absolute: [1, 2, 3, 4] } }))).toEqual([
      1, 2, 3, 4,
    ]);
    expect(absoluteBox(artifact({ id: 1, bounding_box: { absolute: [1, 2, 3] } }))).toBeNull();
    expect(
      absoluteBox(artifact({ id: 1, bounding_box: { absolute: [1, 2, 3, 4, 5] } }))
    ).toBeNull();
    expect(absoluteBox(artifact({ id: 1, bounding_box: { absolute: ["a", 2, 3, 4] } }))).toBeNull();
    expect(absoluteBox(artifact({ id: 1, bounding_box: null }))).toBeNull();
    expect(absoluteBox(artifact({ id: 1 }))).toBeNull();
  });

  it("数字字符串按数字认（上游偶尔把坐标写成字符串）", () => {
    expect(
      absoluteBox(artifact({ id: 1, bounding_box: { absolute: ["1", "2", "3", "4"] } }))
    ).toEqual([1, 2, 3, 4]);
  });
});
