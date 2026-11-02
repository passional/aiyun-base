import { describe, expect, it } from "vitest";

import {
  formatRegionLabel,
  placeholderPattern,
  regionPaletteIndex,
  regionTagStyle,
  splitPromptSegments,
} from "./regions";

describe("region palette", () => {
  it("binds colors to the selection index and wraps after the palette size", () => {
    expect(regionPaletteIndex(1)).toBe(1);
    expect(regionPaletteIndex(8)).toBe(8);
    expect(regionPaletteIndex(9)).toBe(1);
    expect(regionPaletteIndex(0)).toBe(8);
    expect(regionPaletteIndex(Number.NaN)).toBe(1);
  });

  it("derives tag colors from the shared token", () => {
    const style = regionTagStyle(3);
    expect(style["--el-tag-text-color"]).toBe("var(--fa-region-3)");
    expect(style["--el-tag-border-color"]).toBe("var(--fa-region-3)");
    expect(style["--el-tag-bg-color"]).toContain("var(--fa-region-3)");
  });
});

describe("region placeholder", () => {
  it("renders the declared placeholder template", () => {
    expect(formatRegionLabel("区域{n}", 2)).toBe("区域2");
    expect(formatRegionLabel("Area {n}", 12)).toBe("Area 12");
    expect(formatRegionLabel(undefined, 4)).toBe("区域4");
  });

  it("builds the matcher from the declaration instead of hardcoding 区域", () => {
    const pattern = placeholderPattern("Area {n}");
    expect(pattern).not.toBeNull();
    expect("把 Area 3 换成一只猫".match(pattern!)).toEqual(["Area 3"]);
  });

  it("escapes literal parts of the template", () => {
    const pattern = placeholderPattern("[区域]{n}");
    expect(pattern).not.toBeNull();
    expect("[区域]2".match(pattern!)).toEqual(["[区域]2"]);
  });

  it("returns no matcher when the template has no {n}", () => {
    expect(placeholderPattern("区域")).toBeNull();
    expect(placeholderPattern(undefined)).toBeNull();
  });
});

describe("splitPromptSegments", () => {
  it("splits plain text and each region token", () => {
    const segments = splitPromptSegments("把区域1换成一只猫，背景区域2调亮", "区域{n}");
    expect(segments).toEqual([
      { text: "把", index: null },
      { text: "区域1", index: 1 },
      { text: "换成一只猫，背景", index: null },
      { text: "区域2", index: 2 },
      { text: "调亮", index: null },
    ]);
  });

  it("keeps repeated references to the same region as separate segments", () => {
    const regions = splitPromptSegments("区域1 和 区域1", "区域{n}").filter(
      (segment) => segment.index !== null
    );
    expect(regions.map((segment) => segment.index)).toEqual([1, 1]);
  });

  it("does not color anything without a declared template", () => {
    expect(splitPromptSegments("区域1", null)).toEqual([{ text: "区域1", index: null }]);
  });

  it("returns no segments for an empty prompt", () => {
    expect(splitPromptSegments("", "区域{n}")).toEqual([]);
  });
});
