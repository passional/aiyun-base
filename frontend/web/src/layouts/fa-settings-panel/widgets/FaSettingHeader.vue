<template>
  <div>
    <div class="flex items-center justify-between">
      <span class="text-sm font-medium text-g-700">{{ t("setting.title") }}</span>
      <div
        @click="$emit('close')"
        class="flex items-center justify-center cursor-pointer size-7.5 transition-all! duration-200 rounded hover:bg-g-300/80"
      >
        <FaSvgIcon icon="ri:close-fill" class="block text-xl text-g-600" />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from "vue-i18n";
defineOptions({ name: "FaSettingHeader" });

const { t } = useI18n();

interface Emits {
  close: [];
}

defineEmits<Emits>();
</script>
