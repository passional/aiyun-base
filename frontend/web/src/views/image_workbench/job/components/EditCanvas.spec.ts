import { flushPromises, mount } from "@vue/test-utils";
import { computed, nextTick, ref, watch } from "vue";
import { createI18n } from "vue-i18n";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { JobArtifact } from "@/api/module_image_workbench/job";
import EditCanvas from "./EditCanvas.vue";

vi.mock("@/api/module_image_workbench/job", () => ({
  default: { downloadArtifact: vi.fn(), uploadReferenceImage: vi.fn() },
}));

const noPicture = "请先上传参考图，或从历史任务打开一次图层拆分结果";
const imageSources: string[] = [];
const drawImage = vi.fn();
const fillText = vi.fn();
const strokeRect = vi.fn();

class LoadedImage {
  naturalWidth = 1086;
  naturalHeight = 1448;
  crossOrigin = "";
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;

  set src(value: string) {
    imageSources.push(value);
    queueMicrotask(() => this.onload?.());
  }
}

beforeEach(() => {
  imageSources.length = 0;
  drawImage.mockClear();
  fillText.mockClear();
  strokeRect.mockClear();
  vi.stubGlobal("ref", ref);
  vi.stubGlobal("computed", computed);
  vi.stubGlobal("watch", watch);
  vi.stubGlobal("Image", LoadedImage);
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({
    clearRect: vi.fn(),
    drawImage,
    save: vi.fn(),
    restore: vi.fn(),
    beginPath: vi.fn(),
    fill: vi.fn(),
    fillRect: vi.fn(),
    strokeRect,
    fillText,
    measureText: (text: string) => ({ width: text.length * 8 }),
    roundRect: vi.fn(),
    setLineDash: vi.fn(),
  } as unknown as CanvasRenderingContext2D);
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("EditCanvas initial image", () => {
  it.each([
    {
      name: "uploaded reference",
      props: { referencePath: "static/upload/file/reference.png" },
      expectedSources: ["/static/upload/file/reference.png"],
    },
    {
      name: "historical decomposition",
      props: {
        jobId: 42,
        artifacts: [
          { id: 1, is_base: true, z_index: null, image_url: "/base.png" },
          {
            id: 2,
            is_base: false,
            z_index: 1,
            bounding_box: { absolute: [0, 0, 100, 100] },
            image_url: "/layer.png",
          },
        ] satisfies JobArtifact[],
      },
      expectedSources: ["/base.png", "/layer.png"],
    },
  ])("shows the $name image on first open", async ({ props, expectedSources }) => {
    const wrapper = mount(EditCanvas, {
      props: { range: 1000, ...props },
      global: {
        plugins: [createI18n({ legacy: false, locale: "zh", messages: { zh: {} } })],
        stubs: { ElAlert: true, ElButton: true, ElTag: true },
      },
    });
    await flushPromises();
    await nextTick();
    for (const source of expectedSources) {
      expect(imageSources.some((loaded) => loaded.includes(source))).toBe(true);
    }
    expect(drawImage).toHaveBeenCalled();
    expect(wrapper.text()).not.toContain(noPicture);
    wrapper.unmount();
  });
});

describe("EditCanvas selections", () => {
  it("draws every selection with its region label", async () => {
    const wrapper = mount(EditCanvas, {
      props: {
        range: 1000,
        referencePath: "static/upload/file/reference.png",
        placeholderTemplate: "区域{n}",
        activeIndex: 2,
        selections: [
          { index: 1, bbox: [10, 10, 200, 200] },
          { index: 2, bbox: [300, 300, 500, 500] },
        ],
      },
      global: {
        plugins: [createI18n({ legacy: false, locale: "zh", messages: { zh: {} } })],
        stubs: { ElAlert: true, ElButton: true, ElTag: true },
      },
    });
    await flushPromises();
    await nextTick();

    expect(strokeRect.mock.calls.length).toBeGreaterThanOrEqual(2);
    const labels = new Set(fillText.mock.calls.map(([label]) => String(label)));
    expect(labels).toEqual(new Set(["区域1", "区域2"]));
    wrapper.unmount();
  });
});
