<!--
  绘图工作台 - 区域标签：颜色按选区序号固定绑定，与提示词里的区域占位符、
  画布上的框同色（规则见 ../regions.ts）。
-->
<template>
  <ElTag size="small" :style="tagStyle">
    <slot />
  </ElTag>
</template>

<script setup lang="ts">
import { regionTagStyle } from "../regions";

defineOptions({
  name: "ImageWorkbenchRegionTag",
});

const props = defineProps<{
  /** 选区序号；颜色由它决定 */
  index: number;
}>();

const tagStyle = computed(() => regionTagStyle(props.index));
</script>
