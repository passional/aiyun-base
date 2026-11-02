import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import RegionPromptInput from "./RegionPromptInput.vue";

describe("RegionPromptInput", () => {
  it("colors the declared region tokens and exposes the native textarea", () => {
    const wrapper = mount(RegionPromptInput, {
      props: {
        modelValue: "把区域1换成一只猫，背景区域2调亮",
        placeholderTemplate: "区域{n}",
      },
    });

    const tokens = wrapper.findAll(".region-prompt-input__token");
    expect(tokens.map((token) => token.text())).toEqual(["区域1", "区域2"]);
    expect(tokens[0]?.attributes("style")).toContain("var(--fa-region-1)");
    expect(tokens[1]?.attributes("style")).toContain("var(--fa-region-2)");
    expect(wrapper.vm.textarea).toBeInstanceOf(HTMLTextAreaElement);

    wrapper.unmount();
  });

  it("renders the prompt as plain text without a declared placeholder", () => {
    const wrapper = mount(RegionPromptInput, {
      props: { modelValue: "把区域1换成一只猫" },
    });

    expect(wrapper.findAll(".region-prompt-input__token")).toHaveLength(0);
    expect(wrapper.find(".region-prompt-input__mirror").text()).toBe("把区域1换成一只猫");

    wrapper.unmount();
  });
});
