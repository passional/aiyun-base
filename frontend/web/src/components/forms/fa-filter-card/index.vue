<!-- 筛选卡：字段与操作同一行，窄屏自动换行；单字段不留大片空白 -->
<template>
  <section class="fa-filter-card">
    <div class="fa-filter-card__fields">
      <slot />
    </div>
    <div v-if="$slots.actions" class="fa-filter-card__actions">
      <slot name="actions" />
    </div>
  </section>
</template>

<script lang="ts" setup>
defineOptions({ name: "FaFilterCard" });
</script>

<style lang="scss" scoped>
.fa-filter-card {
  display: flex;
  flex-wrap: wrap;
  gap: var(--fa-space-3);
  align-items: flex-end;
  padding: var(--fa-space-4);
  background: var(--fa-surface);
  border: 1px solid var(--fa-border);
  border-radius: var(--fa-radius-md);
  box-shadow: var(--fa-shadow-card);

  &__fields {
    display: flex;
    flex: 1;
    flex-wrap: wrap;
    gap: var(--fa-space-3);
    align-items: flex-end;
    min-width: 560px;
  }

  &__actions {
    display: flex;
    flex: none;
    gap: var(--fa-space-2);
    align-items: center;
  }
}
</style>
