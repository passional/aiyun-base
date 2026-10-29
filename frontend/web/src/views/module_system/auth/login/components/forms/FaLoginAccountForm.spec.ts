/**
 * 登录账号表单 — 验证码回归测试。
 *
 * 后端 /captcha/slider/complete 要求前端提交拼图缺口位置（position），
 * 因此表单必须渲染拼图验证码并把 verify 的位置透传给登录流程。
 * 历史问题是同步上游后仍渲染纯滑块（FaDragVerify），不产生位置，
 * 后端返回「请拖动滑块完成拼图」。
 */
import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import ElementPlus from "element-plus";
import { nextTick } from "vue";
import FaPuzzleCaptcha from "@/components/forms/fa-puzzle-captcha/index.vue";
import FaDragVerify from "@/components/forms/fa-drag-verify/index.vue";
import FaLoginAccountForm from "./FaLoginAccountForm.vue";

const BG_IMAGE = "data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=";
const PIECE_IMAGE = "data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=";

function mountForm() {
  return mount(FaLoginAccountForm, {
    props: {
      loginForm: { username: "", password: "" },
      isPassing: false,
      isClickPass: false,
      rules: {},
      captchaState: {
        enable: true,
        key: "captcha-key",
        img_base: BG_IMAGE,
        piece_base: PIECE_IMAGE,
        piece_y: 40,
        piece_width: 55,
        piece_height: 48,
        bg_width: 320,
        bg_height: 160,
      },
      codeLoading: false,
      formKey: 0,
      isDark: false,
      dragVerifyTextColor: "#666",
      loading: false,
    },
    global: {
      plugins: [ElementPlus],
      components: { FaPuzzleCaptcha, FaDragVerify },
      directives: { ripple: {} },
      mocks: { $t: (key: string) => key },
      stubs: { FaLoginAuthLinkRow: true, FaSvgIcon: true },
    },
  });
}

describe("FaLoginAccountForm — 拼图验证码", () => {
  it("渲染拼图入口而非纯滑块", () => {
    const wrapper = mountForm();

    expect(wrapper.findComponent(FaPuzzleCaptcha).exists()).toBe(true);
    expect(wrapper.find(".fa-puzzle__entry").exists()).toBe(true);
    expect(wrapper.find(".drag_verify").exists()).toBe(false);
  });

  it("打开弹窗后展示服务端下发的拼图背景", async () => {
    const wrapper = mountForm();

    wrapper.findComponent(FaPuzzleCaptcha).vm.open();
    await nextTick();

    const bg = document.body.querySelector(".fa-puzzle__bg");
    expect(bg?.getAttribute("src")).toBe(BG_IMAGE);
  });

  it("拼图 verify 事件把缺口位置透传给登录流程", async () => {
    const wrapper = mountForm();

    wrapper.findComponent(FaPuzzleCaptcha).vm.$emit("verify", 137);
    await nextTick();

    expect(wrapper.emitted("sliderPass")).toEqual([[137]]);
  });
});
