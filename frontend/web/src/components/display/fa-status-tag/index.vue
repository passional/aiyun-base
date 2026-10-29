<template>
  <ElTag :type="type" :size="size" :effect="effect" :round="round" :closable="closable">
    {{ displayLabel }}
  </ElTag>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import { translateStatusText } from "@utils/table/columnLabelI18n";

defineOptions({ name: "FaStatusTag" });

const { t, te } = useI18n();

interface Props {
  /** 显示文本 */
  label?: string | number;
  /**
   * 标签类型（与 EP ElTag 一致）。
   * 不传时为 EP 默认样式（无 type 属性）。
   */
  type?: "primary" | "success" | "warning" | "danger" | "info";
  /** 尺寸 */
  size?: "large" | "default" | "small";
  /** 主题效果：light（默认）/ dark / plain */
  effect?: "light" | "dark" | "plain";
  /** 是否圆角 */
  round?: boolean;
  /** 是否可关闭 */
  closable?: boolean;
}

const props = withDefaults(defineProps<Props>(), {
  label: "",
  type: undefined,
  size: undefined,
  effect: "light",
  round: false,
  closable: false,
});

const displayLabel = computed(() => {
  const text = String(props.label ?? "");
  return translateStatusText(text, (key) => (te(key) ? t(key) : text));
});
</script>
