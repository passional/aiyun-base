<template>
  <p class="fa-section-title" :style="style">
    <span>{{ title }}</span>
  </p>
</template>

<script setup lang="ts">
defineOptions({ name: "FaSectionTitle" });

interface Props {
  title: string;
  style?: Record<string, any>;
}

withDefaults(defineProps<Props>(), {
  style: () => ({}),
});
</script>

<style scoped lang="scss">
.fa-section-title {
  display: flex;
  gap: 10px;
  align-items: center;
  margin: 24px 0 12px;
  font-size: 12px;
  font-weight: 600;
  color: var(--el-text-color-secondary);
  letter-spacing: 0.04em;

  &::after {
    flex: 1;
    height: 1px;
    content: "";
    background: var(--el-border-color-lighter);
  }
}
</style>
