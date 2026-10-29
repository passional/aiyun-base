<!-- 系统 logo：优先使用接口 sys_web_logo，缺省用内置图 -->
<template>
  <div class="flex items-center justify-center">
    <img
      :style="logoStyle"
      :src="resolvedSrc"
      alt="logo"
      class="h-full w-full object-contain"
      @error="onImgError"
    />
  </div>
</template>

<script setup lang="ts">
import defaultLogoUrl from "@fa_imgs/logo.svg";

defineOptions({ name: "FaLogo" });

interface Props {
  /** logo 大小 */
  size?: number | string;
  /** 自定义地址（如配置接口 sys_web_logo）；不传则用默认资源 */
  src?: string;
}

const props = withDefaults(defineProps<Props>(), {
  size: 36,
  src: undefined,
});

const fallbackTriggered = ref(false);

// 官方默认 logo（含官方服务地址）统一用内置新品牌标识替代；
// 管理员自定义上传的 logo 仍优先生效。
const OFFICIAL_LOGO_HOST = "service.fastapiadmin.com";

const resolvedSrc = computed(() => {
  if (fallbackTriggered.value) return defaultLogoUrl;
  const custom = props.src?.trim();
  if (!custom) return defaultLogoUrl;
  if (custom.includes(OFFICIAL_LOGO_HOST)) {
    return defaultLogoUrl;
  }
  return custom;
});

function onImgError() {
  if (!fallbackTriggered.value) {
    fallbackTriggered.value = true;
  }
}

const logoStyle = computed(() => ({ width: `${props.size}px`, height: `${props.size}px` }));

watch(
  () => props.src,
  () => {
    fallbackTriggered.value = false;
  }
);
</script>
