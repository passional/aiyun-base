/**
 * 能力声明求值的用例：逐条覆盖 workbench 表单必须表现的几件事。
 *
 * 清单取自 seed（`backend/sql/image_workbench_model.json` 的 pro 模型），
 * 因为要验证的正是「同一份数据在界面上呈现出参数间的联动」。
 */
import { describe, expect, it } from "vitest";
import type { ModelCapability } from "@/api/module_image_workbench/model";
import { acceptsValue, buildFacts, evaluateCapability } from "./capability";

const PRO: ModelCapability = {
  reference: { supported: true, max_images: 10 },
  modes: [
    {
      key: "layer_decomposition",
      label: { zh: "图层拆分", en: "Layer decomposition" },
      trigger: { param: "layer_decomposition", eq: true },
    },
  ],
  params: [
    {
      key: "size",
      label: { zh: "尺寸", en: "Size" },
      control: "select",
      allowed: ["1K", "1.5K", "2K"],
      default: "2K",
      source: "documented",
      overrides: {
        layer_decomposition: { allowed: ["1K", "1.5K", "2K", "auto"], default: "auto" },
      },
    },
    {
      key: "layer_decomposition",
      label: { zh: "图层拆分", en: "Layer decomposition" },
      control: "switch",
      default: false,
      source: "inferred",
      when: { all: [{ request: "reference_count", eq: 1 }] },
    },
    {
      key: "output_format",
      label: { zh: "输出格式", en: "Output format" },
      control: "select",
      allowed: ["png", "jpeg"],
      default: "png",
      source: "documented",
    },
    {
      key: "background",
      label: { zh: "图片透明通道", en: "Transparent background" },
      control: "select",
      allowed: ["opaque", "transparent"],
      default: "opaque",
      source: "documented",
      when: {
        all: [
          { param: "output_format", not_in: ["jpeg"] },
          { request: "reference_count", eq: 1 },
          { request: "reference_has_alpha", eq: true },
        ],
      },
    },
  ],
};

function paramOf(result: ReturnType<typeof evaluateCapability>, key: string) {
  const param = result.params.find((item) => item.key === key);
  if (!param) throw new Error(`参数 ${key} 不存在`);
  return param;
}

describe("能力声明求值", () => {
  const noReference = buildFacts([]);
  const oneAlphaReference = buildFacts([{ hasAlpha: true }]);

  it("没有参考图时，依赖参考图的参数不可用且不参与取值", () => {
    const result = evaluateCapability(PRO, { facts: noReference });
    expect(paramOf(result, "layer_decomposition").available).toBe(false);
    expect(paramOf(result, "background").available).toBe(false);
    // 普通请求：无参考图、用户什么都没改，其余参数照常可用（design D5 的关键反例）
    expect(paramOf(result, "size").available).toBe(true);
    expect(paramOf(result, "size").defaultValue).toBe("2K");
    expect(paramOf(result, "output_format").available).toBe(true);
  });

  it("参考图恰好一张且带透明通道时，透明通道可用；换成 jpeg 后不可用", () => {
    const available = evaluateCapability(PRO, {
      values: { output_format: "png" },
      facts: oneAlphaReference,
    });
    expect(paramOf(available, "background").available).toBe(true);
    expect(paramOf(available, "background").allowed).toEqual(["opaque", "transparent"]);
    expect(paramOf(available, "background").defaultValue).toBe("opaque");

    const blocked = evaluateCapability(PRO, {
      values: { output_format: "jpeg" },
      facts: oneAlphaReference,
    });
    const background = paramOf(blocked, "background");
    expect(background.available).toBe(false);
    // 「为何不可用」只列当前不满足的那一条
    expect(background.unsatisfied).toEqual([
      { kind: "param", key: "output_format", operator: "not_in", right: ["jpeg"] },
    ]);
  });

  it("参考图不带透明通道时，透明通道不可用", () => {
    const facts = buildFacts([{ hasAlpha: false }]);
    const result = evaluateCapability(PRO, { facts });
    expect(paramOf(result, "background").available).toBe(false);
    expect(paramOf(result, "background").unsatisfied.map((item) => item.key)).toContain(
      "reference_has_alpha"
    );
  });

  it("启用图层拆分后，尺寸按该出图方式覆盖：多出 auto 且默认值随之改变", () => {
    const result = evaluateCapability(PRO, {
      values: { layer_decomposition: true },
      facts: oneAlphaReference,
    });
    expect(result.activeMode).toBe("layer_decomposition");
    const size = paramOf(result, "size");
    expect(size.allowed).toEqual(["1K", "1.5K", "2K", "auto"]);
    expect(size.defaultValue).toBe("auto");

    // 未启用时回到条目自身的取值域与默认值
    const plain = evaluateCapability(PRO, { facts: oneAlphaReference });
    expect(plain.activeMode).toBeNull();
    expect(paramOf(plain, "size").allowed).toEqual(["1K", "1.5K", "2K"]);
    expect(paramOf(plain, "size").defaultValue).toBe("2K");
  });

  it("请求事实的封闭集合：不认识的左值按不满足处理", () => {
    const unknownFact: ModelCapability = {
      ...PRO,
      params: [
        {
          ...PRO.params[0]!,
          when: { all: [{ request: "unknown_fact" as never, eq: 1 }] },
        },
      ],
    };
    expect(
      paramOf(evaluateCapability(unknownFact, { facts: oneAlphaReference }), "size").available
    ).toBe(false);
  });

  it("取值域判定：开关只收布尔、自定义尺寸按形态收", () => {
    expect(acceptsValue("switch", [], false, true)).toBe(true);
    expect(acceptsValue("switch", [], false, "true")).toBe(false);
    expect(acceptsValue("select", ["1K", "2K"], false, "2K")).toBe(true);
    expect(acceptsValue("select", ["1K", "2K"], false, "4K")).toBe(false);
    expect(acceptsValue("select", ["1K"], true, "1024x1024")).toBe(true);
    expect(acceptsValue("number", [], false, 3)).toBe(true);
    expect(acceptsValue("number", [], false, "3")).toBe(false);
  });
});
