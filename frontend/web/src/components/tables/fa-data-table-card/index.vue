<!-- 表格卡：工具栏 + 表格 + 分页 的固定外壳 -->
<template>
  <ElCard class="fa-data-table-card" shadow="never">
    <div v-if="$slots.toolbar" class="fa-data-table-card__toolbar">
      <slot name="toolbar" />
    </div>
    <div class="fa-data-table-card__body">
      <slot />
    </div>
    <div v-if="$slots.footer" class="fa-data-table-card__footer">
      <slot name="footer" />
    </div>
  </ElCard>
</template>

<script lang="ts" setup>
defineOptions({ name: "FaDataTableCard" });
</script>

<style lang="scss" scoped>
.fa-data-table-card {
  background: var(--fa-surface);
  border: 1px solid var(--fa-border);
  border-radius: var(--fa-radius-md);
  box-shadow: var(--fa-shadow-card);

  :deep(.el-card__body) {
    display: flex;
    flex-direction: column;
    min-width: 0;
    padding: 0;
  }

  &__toolbar {
    display: flex;
    flex-wrap: wrap;
    gap: var(--fa-space-2);
    align-items: center;
    padding: var(--fa-space-3) var(--fa-space-4);
    border-bottom: 1px solid var(--fa-border-subtle);
  }

  &__body {
    min-width: 0;
  }

  &__footer {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    padding: var(--fa-space-3) var(--fa-space-4);
    border-top: 1px solid var(--fa-border-subtle);
  }
}
</style>
