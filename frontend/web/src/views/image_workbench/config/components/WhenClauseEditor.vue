<!--
  绘图工作台 - 能力声明编辑：条件依赖的单个条件（design D2 的封闭词表）。

  左值二选一（另一个参数 / 本次请求的事实），算子四选一（eq / ne / in / not_in），
  **不提供 or / not 的组合**——已知规则全是合取，一旦放开等于开始写一个小型表达式语言。
  左值一变，右值的类型随之改变（参数是开关就要布尔、是数字就要数字），因此左值改动时
  右值重置为该类型下的合理初值，而不是留着上一个左值的值。
-->
<template>
  <div class="flex flex-wrap items-end gap-2">
    <label class="flex flex-col gap-1">
      <span class="text-xs text-(--el-text-color-secondary)">
        {{ t("image_workbench.model.clauseLeft") }}
      </span>
      <ElSelect v-model="kind" class="w-32">
        <ElOption :label="t('image_workbench.model.clauseLeftParam')" value="param" />
        <ElOption :label="t('image_workbench.model.clauseLeftRequest')" value="request" />
      </ElSelect>
    </label>

    <label class="flex flex-col gap-1">
      <span class="text-xs text-(--el-text-color-secondary)">
        {{ t("image_workbench.model.clauseLeftKey") }}
      </span>
      <ElSelect v-model="leftKey" class="w-44">
        <ElOption
          v-for="option in leftOptions"
          :key="option.value"
          :label="option.label"
          :value="option.value"
        />
      </ElSelect>
    </label>

    <label class="flex flex-col gap-1">
      <span class="text-xs text-(--el-text-color-secondary)">
        {{ t("image_workbench.model.clauseOperator") }}
      </span>
      <ElSelect v-model="operator" class="w-28">
        <ElOption
          v-for="item in OPERATORS"
          :key="item"
          :label="t(`image_workbench.model.op_${item}`)"
          :value="item"
        />
      </ElSelect>
    </label>

    <label class="flex flex-col gap-1">
      <span class="text-xs text-(--el-text-color-secondary)">
        {{ t("image_workbench.model.clauseRight") }}
      </span>
      <!-- in / not_in 的右值是一组取值，逐项填写；eq / ne 与左值同型 -->
      <ElInputTag
        v-if="isListOperator"
        :model-value="toTagValues(rightValue)"
        :placeholder="t('image_workbench.model.clauseRightListPlaceholder')"
        class="w-52"
        @update:model-value="onListValue"
      />
      <ParamValueInput
        v-else
        :model-value="rightValue"
        :control="rightControl"
        :allowed="rightAllowed"
        class="w-52"
        @update:model-value="rightValue = $event"
      />
    </label>

    <ElButton text type="danger" :icon="Delete" @click="emit('remove')" />
  </div>
</template>

<script setup lang="ts">
import { Delete } from "@element-plus/icons-vue";
import type {
  CapabilityParam,
  CapabilityWhenClause,
  ParamControl,
  RequestFact,
} from "@/api/module_image_workbench/model";
import { parseTagValues, toTagValues } from "../../values";
import ParamValueInput from "../../components/ParamValueInput.vue";

defineOptions({
  name: "ImageWorkbenchWhenClauseEditor",
  inheritAttrs: false,
});

/** 待编辑的条件（就地编辑：它属于父组件的清单数据，本组件只负责这一个条件的编排） */
const clause = defineModel<CapabilityWhenClause>({ required: true });

const props = defineProps<{
  /** 同一份清单里的参数条目：左值选参数时从这里取，右值的形态也随之确定 */
  params: CapabilityParam[];
}>();

const emit = defineEmits<{ (e: "remove"): void }>();

const { t } = useI18n();

type WhenOperator = "eq" | "ne" | "in" | "not_in";
type LeftKind = "param" | "request";

const OPERATORS: WhenOperator[] = ["eq", "ne", "in", "not_in"];
const REQUEST_FACTS: RequestFact[] = ["reference_count", "reference_has_alpha"];

/** 请求事实只有两项，集合封闭（design D2）：不认识的事实一律按不满足处理 */
const REQUEST_FACT_I18N: Record<RequestFact, string> = {
  reference_count: "fact_reference_count",
  reference_has_alpha: "fact_reference_has_alpha",
};

function paramByKey(key?: string | null): CapabilityParam | undefined {
  return props.params.find((item) => item.key === key);
}

const kind = computed<LeftKind>({
  get: () => (clause.value.request !== undefined ? "request" : "param"),
  set: (value: LeftKind) =>
    applyLeft(value, value === "request" ? "reference_count" : (props.params[0]?.key ?? "")),
});

const leftKey = computed({
  get: () => clause.value.request ?? clause.value.param ?? "",
  set: (value: string) => applyLeft(kind.value, value),
});

const leftOptions = computed(() =>
  kind.value === "request"
    ? REQUEST_FACTS.map((fact) => ({
        label: t(`image_workbench.model.${REQUEST_FACT_I18N[fact]}`),
        value: fact as string,
      }))
    : props.params
        .filter((item) => item.key)
        .map((item) => ({ label: item.label?.zh || item.key, value: item.key }))
);

const operator = computed({
  get: (): WhenOperator => {
    if (clause.value.eq !== undefined) return "eq";
    if (clause.value.ne !== undefined) return "ne";
    if (clause.value.in !== undefined) return "in";
    if (clause.value.not_in !== undefined) return "not_in";
    return "eq";
  },
  set: (value: WhenOperator) => setOperator(value),
});

const rightValue = computed({
  get: () => clause.value[operator.value],
  set: (value: unknown) => writeRight(operator.value, value),
});

const isListOperator = computed(() => operator.value === "in" || operator.value === "not_in");

const rightControl = computed<ParamControl>(() => {
  if (clause.value.request === "reference_count") return "number";
  if (clause.value.request === "reference_has_alpha") return "switch";
  return paramByKey(clause.value.param)?.control ?? "text";
});

const rightAllowed = computed(() =>
  clause.value.request === undefined ? (paramByKey(clause.value.param)?.allowed ?? []) : []
);

/** 右值在该左值形态下的合理初值：开关取真、数字取 0、单选取域内第一项 */
function initialRight(kindValue: LeftKind, key: string): unknown {
  if (kindValue === "request") return key === "reference_count" ? 1 : true;
  const param = paramByKey(key);
  if (param?.control === "select") return param.allowed?.[0] ?? "";
  if (param?.control === "multiselect") return [];
  if (param?.control === "number") return 0;
  if (param?.control === "switch") return true;
  return "";
}

/** 换左值：左值一变，右值的类型与含义都不再对应，必须一并重置 */
function applyLeft(nextKind: LeftKind, nextKey: string): void {
  const next: CapabilityWhenClause =
    nextKind === "request" ? { request: nextKey as RequestFact } : { param: nextKey };
  next.eq = initialRight(nextKind, nextKey);
  clause.value = next;
}

/** 换算子：取值个数会变（in / not_in 要一组），在旧值形态允许时就地转换 */
function setOperator(next: WhenOperator): void {
  const previous = rightValue.value;
  clearOperators();
  if (next === "in" || next === "not_in") {
    writeRight(next, Array.isArray(previous) ? previous : previous == null ? [] : [previous]);
    return;
  }
  const fallback = initialRight(kind.value, leftKey.value);
  writeRight(next, Array.isArray(previous) ? (previous[0] ?? fallback) : (previous ?? fallback));
}

function onListValue(tags?: string[]): void {
  writeRight(operator.value, parseTagValues(tags, rightControl.value));
}

/** 按算子写入右值：逐分支写而不是用联合键索引，值的形态才与字段声明对得上 */
function writeRight(target: WhenOperator, value: unknown): void {
  if (target === "in" || target === "not_in") {
    clause.value[target] = (Array.isArray(value) ? value : []) as unknown[];
    return;
  }
  if (target === "eq") clause.value.eq = value;
  else clause.value.ne = value;
}

function clearOperators(): void {
  delete clause.value.eq;
  delete clause.value.ne;
  delete clause.value.in;
  delete clause.value.not_in;
}
</script>
