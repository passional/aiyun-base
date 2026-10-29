<!-- 指标卡：标签 + 数值 + 趋势/说明（数字使用等宽，避免跳动） -->
<template>
  <div class="fa-stat-card">
    <div class="fa-stat-card__label">{{ label }}</div>
    <div class="fa-stat-card__value fa-tnum">{{ value }}</div>
    <div v-if="hint" class="fa-stat-card__hint" :class="`is-${trend}`">{{ hint }}</div>
  </div>
</template>

<script lang="ts" setup>
defineOptions({ name: "FaStatCard" });

withDefaults(
  defineProps<{
    label: string;
    value: string | number;
    hint?: string;
    trend?: "up" | "down" | "flat";
  }>(),
  { hint: "", trend: "flat" }
);
</script>

<style lang="scss" scoped>
.fa-stat-card {
  padding: var(--fa-space-4);
  background: var(--fa-surface);
  border: 1px solid var(--fa-border);
  border-radius: var(--fa-radius-md);
  box-shadow: var(--fa-shadow-card);

  &__label {
    font-size: var(--fa-font-sm);
    color: var(--el-text-color-secondary);
  }

  &__value {
    margin-top: var(--fa-space-2);
    font-size: 24px;
    font-weight: 600;
    line-height: 1.2;
    color: var(--el-text-color-primary);
  }

  &__hint {
    margin-top: var(--fa-space-2);
    font-size: var(--fa-font-sm);
    color: var(--el-text-color-secondary);

    &.is-up {
      color: var(--el-color-success);
    }

    &.is-down {
      color: var(--el-color-danger);
    }
  }
}
</style>
