<!--
  绘图工作台 - 能力声明编辑：按参数的 control 渲染取值编辑器。

  默认值、出图方式的触发取值、条件依赖的右值三处共用同一个控件——同一个 control
  在界面上永远长成同一个样子，管理员不会遇到「这里是下拉、那里是输入框」。
-->
<template>
  <ElSwitch
    v-if="control === 'switch'"
    :model-value="modelValue === true"
    :disabled="disabled"
    @update:model-value="emitValue"
  />
  <ElInputNumber
    v-else-if="control === 'number'"
    :model-value="numberValue"
    :disabled="disabled"
    :controls="false"
    :placeholder="placeholder"
    class="w-full"
    @update:model-value="emitValue"
  />
  <ElInput
    v-else-if="control === 'text'"
    :model-value="textValue"
    :disabled="disabled"
    :placeholder="placeholder"
    clearable
    @update:model-value="emitValue"
  />
  <ElSelect
    v-else
    :model-value="selectValue"
    :multiple="control === 'multiselect'"
    :disabled="disabled"
    :placeholder="placeholder"
    :filterable="allowCustom"
    :allow-create="allowCustom"
    :default-first-option="allowCustom"
    clearable
    class="w-full"
    @update:model-value="emitValue"
  >
    <ElOption
      v-for="option in options"
      :key="option.key"
      :label="option.label"
      :value="option.value"
    />
  </ElSelect>
</template>

<script setup lang="ts">
import type { ParamControl } from "@/api/module_image_workbench/model";
import { formatValue } from "../values";

defineOptions({
  name: "ImageWorkbenchParamValueInput",
});

const props = withDefaults(
  defineProps<{
    modelValue?: unknown;
    /** 参数的取值形态：决定用哪个控件，也决定取值如何归一 */
    control: ParamControl;
    /** 单选 / 多选的取值域 */
    allowed?: unknown[] | null;
    /** 单选时是否额外允许填自定义「宽x高」 */
    allowCustom?: boolean;
    placeholder?: string;
    disabled?: boolean;
  }>(),
  {
    modelValue: undefined,
    allowed: null,
    allowCustom: false,
    placeholder: "",
    disabled: false,
  }
);

const emit = defineEmits<{ (e: "update:modelValue", value: unknown): void }>();

const options = computed(() =>
  (props.allowed ?? []).map((value, index) => ({
    // 取值本身可能格式化后撞车（数字 1 与字符串 "1"），用下标保证键唯一
    key: `${index}-${formatValue(value)}`,
    label: formatValue(value),
    // 取值是任意 JSON 值，ElOption 的 value 类型收敛不了，按 any 交给它

    value: value as any,
  }))
);

/**
 * 多选的绑定值必须恒为数组；单选取原值（未设置时留空显示占位符）。
 * 取值是任意 JSON 值（可能是数字、布尔），ElSelect 的绑定类型因此按 any 处理。
 */

const selectValue = computed<any>(() => {
  if (props.control === "multiselect") {
    return Array.isArray(props.modelValue) ? props.modelValue : [];
  }
  return props.modelValue ?? undefined;
});

const numberValue = computed(() =>
  typeof props.modelValue === "number" ? props.modelValue : undefined
);

const textValue = computed(() => (props.modelValue == null ? "" : String(props.modelValue)));

function emitValue(value: unknown) {
  emit("update:modelValue", value);
}
</script>
