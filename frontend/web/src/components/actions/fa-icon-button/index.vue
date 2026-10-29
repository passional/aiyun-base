<!-- 按钮组件 -->
<template>
  <button
    type="button"
    class="fa-icon-button size-8.5 inline-flex items-center justify-center cursor-pointer border-0 bg-transparent p-0 text-g-600 dark:text-g-800 text-xl rounded-lg transition duration-300 hover:bg-hover-color"
    :class="{ 'rounded-full': circle }"
    :aria-label="ariaLabel || icon"
    :title="ariaLabel || icon"
  >
    <FaSvgIcon :icon="icon"></FaSvgIcon>
    <slot></slot>
  </button>
</template>

<script lang="ts" setup>
defineOptions({ name: "FaIconButton" });

interface Props {
  /** 图标名称 */
  icon: string;
  /** 圆角按钮 */
  circle?: boolean;
  /** 无障碍标签 / 悬停说明 */
  ariaLabel?: string;
}

withDefaults(defineProps<Props>(), {});
</script>

<style lang="scss" scoped>
.fa-icon-button {
  &:hover :deep(.fa-svg-icon) {
    color: var(--el-color-primary);
  }
}
</style>
