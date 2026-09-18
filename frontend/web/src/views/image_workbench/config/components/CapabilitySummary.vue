<!--
  绘图工作台 - 能力声明的只读呈现（模型详情的「能力声明」一栏）。

  逐条列出参数的全部知识：取值形态、取值域、默认值、条件依赖、按出图方式的覆盖，
  以及每个取值的来源标记。未实测的取值在这里同样可辨认（spec: 能力声明的预置）。
-->
<template>
  <div v-if="!capability" class="text-xs text-(--el-text-color-secondary)">—</div>
  <div v-else class="space-y-2">
    <div class="flex flex-wrap items-center gap-2">
      <span class="text-xs text-(--el-text-color-secondary)">
        {{ t("image_workbench.model.referenceSection") }}：
      </span>
      <FaStatusTag
        :type="capability.reference?.supported ? 'success' : 'info'"
        :label="referenceText"
      />
    </div>

    <div class="flex flex-wrap items-center gap-2">
      <span class="text-xs text-(--el-text-color-secondary)">
        {{ t("image_workbench.model.modesSection") }}：
      </span>
      <template v-if="modes.length > 0">
        <ElTag v-for="mode in modes" :key="mode.key" size="small" type="primary">
          {{ mode.label?.zh || mode.key }}
        </ElTag>
      </template>
      <span v-else class="text-xs text-(--el-text-color-secondary)">
        {{ t("image_workbench.model.modesEmpty") }}
      </span>
    </div>

    <div>
      <span class="text-xs text-(--el-text-color-secondary)">
        {{ t("image_workbench.model.paramsSection") }}：
      </span>
      <span v-if="params.length === 0" class="text-xs text-(--el-text-color-secondary)">
        {{ t("image_workbench.model.paramsEmpty") }}
      </span>
      <div
        v-for="param in params"
        :key="param.key"
        class="mt-1 rounded bg-(--el-fill-color-light) p-2"
      >
        <div class="flex flex-wrap items-center gap-2">
          <span class="font-medium">{{ param.label?.zh || param.key }}</span>
          <ElTag size="small">
            {{ t(`image_workbench.model.control_${param.control}`) }}
          </ElTag>
          <ElTag size="small" :type="sourceTagType(param.source)">
            {{ t(`image_workbench.model.source_${param.source ?? "documented"}`) }}
          </ElTag>
          <span class="text-xs text-(--el-text-color-secondary)">{{ param.key }}</span>
        </div>
        <div class="mt-1 space-y-0.5 text-xs text-(--el-text-color-secondary)">
          <div v-if="domainText(param)">
            {{ t("image_workbench.model.allowed") }}：{{ domainText(param) }}
          </div>
          <div>{{ t("image_workbench.model.defaultValue") }}：{{ formatValue(param.default) }}</div>
          <div v-if="param.when">
            {{ t("image_workbench.model.whenSection") }}：{{ whenText(param) }}
          </div>
          <div v-for="mode in overrideModes(param)" :key="mode.key">
            {{ t("image_workbench.model.overrideLabel", { mode: mode.label?.zh || mode.key }) }}：{{
              overrideText(param, mode)
            }}
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import type {
  CapabilityMode,
  CapabilityParam,
  CapabilityWhenClause,
  ModelCapability,
  ParamSource,
} from "@/api/module_image_workbench/model";
import { formatValue } from "../../values";

defineOptions({
  name: "ImageWorkbenchCapabilitySummary",
  inheritAttrs: false,
});

const props = defineProps<{ capability?: ModelCapability | null }>();

const { t } = useI18n();

const params = computed(() => props.capability?.params ?? []);
const modes = computed(() => props.capability?.modes ?? []);

const referenceText = computed(() => {
  const reference = props.capability?.reference;
  if (!reference?.supported) return t("image_workbench.model.referenceUnsupported");
  return t("image_workbench.model.referenceSupportedValue", { count: reference.max_images ?? 0 });
});

function sourceTagType(source?: ParamSource) {
  if (source === "inferred") return "warning";
  if (source === "measured") return "success";
  return "info";
}

function domainText(param: CapabilityParam) {
  const allowed = (param.allowed ?? []).map((item) => formatValue(item)).join("、");
  if (!allowed) return "";
  return param.allow_custom
    ? t("image_workbench.model.domainWithCustom", { values: allowed })
    : allowed;
}

function labelOf(key: string) {
  return params.value.find((param) => param.key === key)?.label?.zh || key;
}

/** 条件依赖 → 一行文案（只呈现声明，不在这里判断当前是否满足） */
function whenText(param: CapabilityParam) {
  const clauses = param.when?.all ?? [];
  if (clauses.length === 0) return "—";
  return clauses
    .map((clause) => {
      const name = clause.param
        ? `「${labelOf(clause.param)}」`
        : `「${t(`image_workbench.model.fact_${clause.request ?? "reference_count"}`)}」`;
      return `${name}${t(`image_workbench.model.op_${operatorOf(clause)}`)}${rightText(clause)}`;
    })
    .join(` ${t("image_workbench.model.whenJoin")} `);
}

function operatorOf(clause: CapabilityWhenClause) {
  if (clause.eq !== undefined) return "eq";
  if (clause.ne !== undefined) return "ne";
  if (clause.in !== undefined) return "in";
  if (clause.not_in !== undefined) return "not_in";
  return "eq";
}

function rightText(clause: CapabilityWhenClause) {
  const value = clause[operatorOf(clause)];
  if (Array.isArray(value)) {
    return `「${value.map((item) => formatValue(item)).join("、")}」`;
  }
  return `「${formatValue(value)}」`;
}

function overrideModes(param: CapabilityParam): CapabilityMode[] {
  const overrides = param.overrides ?? {};
  return modes.value.filter((mode) => overrides[mode.key]);
}

function overrideText(param: CapabilityParam, mode: CapabilityMode) {
  const override = param.overrides?.[mode.key];
  if (!override) return "—";
  const parts: string[] = [];
  if (override.allowed?.length) {
    parts.push(
      `${t("image_workbench.model.allowed")}：${override.allowed.map((item) => formatValue(item)).join("、")}`
    );
  }
  if (override.default !== undefined && override.default !== null) {
    parts.push(`${t("image_workbench.model.defaultValue")}：${formatValue(override.default)}`);
  }
  return parts.join("；") || "—";
}
</script>
