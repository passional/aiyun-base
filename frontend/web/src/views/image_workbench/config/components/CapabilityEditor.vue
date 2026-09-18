<!--
  绘图工作台 - 能力声明的编辑表单（design D1/D2/D3）。

  能力声明是**数据**：模型支持哪些参数、取值范围、默认值、何时可用、有哪些出图方式。
  这里按它的结构逐项编排，编辑结果整体覆盖保存（后端 Pydantic 会拒掉不合规的声明，
  因此本组件只提示明显缺漏，不做第二套校验——两套规则必然各说各话）。

  两条在实现里容易被做错、这里刻意处理的约定：
  1. **参数键就是上游字段路径**（D4）：键改个名，指向它的引用（出图方式的触发、其它
     参数的条件依赖）跟着改，否则保存时会被后端以「悬空引用」拒绝。
  2. **出图方式是派生结果**（D3）：这里只声明「哪个参数取哪个值进入该方式」，不产出
     任何独立的 mode 字段。
-->
<template>
  <div class="space-y-4">
    <ElAlert v-if="problems.length > 0" type="warning" :closable="false" show-icon>
      <div v-for="(problem, index) in problems" :key="index">{{ problem }}</div>
    </ElAlert>

    <!-- 参考图 -->
    <div class="rounded border border-(--el-border-color) p-3">
      <div class="mb-2 font-medium">{{ t("image_workbench.model.referenceSection") }}</div>
      <div class="grid grid-cols-1 gap-3 md:grid-cols-2">
        <label class="flex flex-col gap-1">
          <span class="text-xs text-(--el-text-color-secondary)">
            {{ t("image_workbench.model.supportReference") }}
          </span>
          <ElSwitch v-model="capability.reference.supported" />
        </label>
        <label v-if="capability.reference.supported" class="flex flex-col gap-1">
          <span class="text-xs text-(--el-text-color-secondary)">
            {{ t("image_workbench.model.maxReferenceImages") }}
          </span>
          <ElInputNumber
            v-model="capability.reference.max_images"
            :min="0"
            :max="64"
            :controls="false"
            class="w-full"
          />
        </label>
      </div>
    </div>

    <!-- 出图方式 -->
    <div class="rounded border border-(--el-border-color) p-3">
      <div class="mb-2 flex items-center justify-between gap-2">
        <div>
          <div class="font-medium">{{ t("image_workbench.model.modesSection") }}</div>
          <div class="text-xs text-(--el-text-color-secondary)">
            {{ t("image_workbench.model.modesHint") }}
          </div>
        </div>
        <ElButton size="small" :icon="Plus" @click="addMode">
          {{ t("image_workbench.model.addMode") }}
        </ElButton>
      </div>

      <ElEmpty
        v-if="capability.modes.length === 0"
        :description="t('image_workbench.model.modesEmpty')"
        :image-size="48"
      />

      <div
        v-for="(mode, index) in capability.modes"
        :key="index"
        class="mb-2 rounded bg-(--el-fill-color-light) p-3"
      >
        <div class="flex flex-wrap items-end gap-3">
          <label class="flex flex-col gap-1">
            <span class="text-xs text-(--el-text-color-secondary)">
              {{ t("image_workbench.model.modeKey") }}
            </span>
            <ElInput
              v-model="mode.key"
              class="w-40"
              :placeholder="t('image_workbench.model.modeKeyPlaceholder')"
              @focus="editingModeKey = mode.key"
              @change="renameModeReferences(editingModeKey, mode.key)"
            />
          </label>
          <label class="flex flex-col gap-1">
            <span class="text-xs text-(--el-text-color-secondary)">
              {{ t("image_workbench.model.labelZh") }}
            </span>
            <ElInput v-model="mode.label.zh" class="w-32" />
          </label>
          <label class="flex flex-col gap-1">
            <span class="text-xs text-(--el-text-color-secondary)">
              {{ t("image_workbench.model.labelEn") }}
            </span>
            <ElInput v-model="mode.label.en" class="w-32" />
          </label>
          <label class="flex flex-col gap-1">
            <span class="text-xs text-(--el-text-color-secondary)">
              {{ t("image_workbench.model.modeTrigger") }}
            </span>
            <ElSwitch
              :model-value="!!mode.trigger"
              @update:model-value="(value) => setModeTrigger(mode, !!value)"
            />
          </label>
          <template v-if="mode.trigger">
            <label class="flex flex-col gap-1">
              <span class="text-xs text-(--el-text-color-secondary)">
                {{ t("image_workbench.model.modeTriggerParam") }}
              </span>
              <ElSelect
                :model-value="mode.trigger.param"
                class="w-48"
                @update:model-value="(value) => setTriggerParam(mode, String(value))"
              >
                <ElOption
                  v-for="param in declaredParams"
                  :key="param.key"
                  :label="param.label?.zh || param.key"
                  :value="param.key"
                />
              </ElSelect>
            </label>
            <label class="flex flex-col gap-1">
              <span class="text-xs text-(--el-text-color-secondary)">
                {{ t("image_workbench.model.modeTriggerValue") }}
              </span>
              <ParamValueInput
                :model-value="mode.trigger.eq"
                :control="triggerControl(mode)"
                :allowed="triggerAllowed(mode)"
                class="w-40"
                @update:model-value="(value) => setTriggerValue(mode, value)"
              />
            </label>
          </template>
          <!-- 固有方式靠坐标表达位置，因此必须给出占位形态与坐标标记写法（否则后端拒绝保存） -->
          <template v-else>
            <label class="flex flex-col gap-1">
              <span class="text-xs text-(--el-text-color-secondary)">
                {{ t("image_workbench.model.markerPlaceholder") }}
              </span>
              <ElInput
                v-model="markerOf(mode).placeholder"
                class="w-32"
                :placeholder="t('image_workbench.model.markerPlaceholderHint')"
              />
            </label>
            <label class="flex flex-col gap-1">
              <span class="text-xs text-(--el-text-color-secondary)">
                {{ t("image_workbench.model.markerTemplate") }}
              </span>
              <ElInput
                v-model="markerOf(mode).template"
                class="w-64"
                :placeholder="t('image_workbench.model.markerTemplateHint')"
              />
            </label>
            <label class="flex flex-col gap-1">
              <span class="text-xs text-(--el-text-color-secondary)">
                {{ t("image_workbench.model.markerRange") }}
              </span>
              <ElInputNumber v-model="markerOf(mode).range" :min="2" :max="100000" class="w-28" />
            </label>
          </template>
          <ElButton text type="danger" :icon="Delete" @click="removeMode(index)" />
        </div>
        <div v-if="!mode.trigger" class="mt-1 text-xs text-(--el-text-color-secondary)">
          {{ t("image_workbench.model.modeFixedHint") }}
        </div>
      </div>
    </div>

    <!-- 参数条目 -->
    <div class="rounded border border-(--el-border-color) p-3">
      <div class="mb-2 flex items-center justify-between gap-2">
        <div>
          <div class="font-medium">{{ t("image_workbench.model.paramsSection") }}</div>
          <div class="text-xs text-(--el-text-color-secondary)">
            {{ t("image_workbench.model.paramsHint") }}
          </div>
        </div>
        <ElButton size="small" :icon="Plus" @click="addParam">
          {{ t("image_workbench.model.addParam") }}
        </ElButton>
      </div>

      <ElEmpty
        v-if="capability.params.length === 0"
        :description="t('image_workbench.model.paramsEmpty')"
        :image-size="48"
      />

      <div
        v-for="(param, index) in capability.params"
        :key="index"
        class="mb-3 rounded border border-(--el-border-color) p-3"
      >
        <div class="mb-2 flex flex-wrap items-center justify-between gap-2">
          <div class="flex flex-wrap items-center gap-2">
            <span class="font-medium">{{ paramTitle(param, index) }}</span>
            <ElTag size="small" :type="sourceTagType(param.source)">
              {{ sourceLabel(param.source) }}
            </ElTag>
          </div>
          <div class="flex items-center gap-1">
            <ElButton
              text
              size="small"
              :icon="ArrowUp"
              :disabled="index === 0"
              @click="moveParam(index, -1)"
            />
            <ElButton
              text
              size="small"
              :icon="ArrowDown"
              :disabled="index === capability.params.length - 1"
              @click="moveParam(index, 1)"
            />
            <ElButton text size="small" type="danger" :icon="Delete" @click="removeParam(index)" />
          </div>
        </div>

        <div class="grid grid-cols-1 gap-3 md:grid-cols-3">
          <label class="flex flex-col gap-1">
            <span class="text-xs text-(--el-text-color-secondary)">
              {{ t("image_workbench.model.paramKey") }}
            </span>
            <ElInput
              v-model="param.key"
              :placeholder="t('image_workbench.model.paramKeyPlaceholder')"
              @focus="editingParamKey = param.key"
              @change="renameParamReferences(editingParamKey, param.key)"
            />
          </label>
          <label class="flex flex-col gap-1">
            <span class="text-xs text-(--el-text-color-secondary)">
              {{ t("image_workbench.model.labelZh") }}
            </span>
            <ElInput v-model="param.label.zh" />
          </label>
          <label class="flex flex-col gap-1">
            <span class="text-xs text-(--el-text-color-secondary)">
              {{ t("image_workbench.model.labelEn") }}
            </span>
            <ElInput v-model="param.label.en" />
          </label>
          <label class="flex flex-col gap-1">
            <span class="text-xs text-(--el-text-color-secondary)">
              {{ t("image_workbench.model.control") }}
            </span>
            <ElSelect v-model="param.control" @change="onControlChange(param)">
              <ElOption
                v-for="control in CONTROLS"
                :key="control"
                :label="t(`image_workbench.model.control_${control}`)"
                :value="control"
              />
            </ElSelect>
          </label>
          <label class="flex flex-col gap-1">
            <span class="text-xs text-(--el-text-color-secondary)">
              {{ t("image_workbench.model.source") }}
            </span>
            <ElSelect v-model="param.source">
              <ElOption
                v-for="source in SOURCES"
                :key="source"
                :label="t(`image_workbench.model.source_${source}`)"
                :value="source"
              />
            </ElSelect>
          </label>
          <label v-if="acceptsCustom(param)" class="flex flex-col gap-1">
            <span class="text-xs text-(--el-text-color-secondary)">
              {{ t("image_workbench.model.allowCustom") }}
            </span>
            <ElSwitch v-model="param.allow_custom" />
          </label>
        </div>

        <div class="mt-3 grid grid-cols-1 gap-3 md:grid-cols-2">
          <label v-if="hasDomain(param)" class="flex flex-col gap-1">
            <span class="text-xs text-(--el-text-color-secondary)">
              {{ t("image_workbench.model.allowed") }}
            </span>
            <ElInputTag
              :model-value="toTagValues(param.allowed)"
              :placeholder="t('image_workbench.model.allowedPlaceholder')"
              @update:model-value="(tags) => onAllowedChange(param, tags)"
            />
          </label>
          <label class="flex flex-col gap-1">
            <span class="text-xs text-(--el-text-color-secondary)">
              {{ t("image_workbench.model.defaultValue") }}
            </span>
            <ParamValueInput
              :model-value="param.default"
              :control="param.control"
              :allowed="param.allowed"
              :allow-custom="!!param.allow_custom"
              @update:model-value="(value) => (param.default = value)"
            />
          </label>
        </div>

        <!-- 条件依赖 -->
        <div class="mt-3">
          <div class="flex flex-wrap items-center gap-2">
            <span class="text-xs text-(--el-text-color-secondary)">
              {{ t("image_workbench.model.whenSection") }}
            </span>
            <ElSwitch
              :model-value="!!param.when"
              @update:model-value="(value) => setWhenEnabled(param, !!value)"
            />
            <span class="text-xs text-(--el-text-color-secondary)">
              {{
                param.when
                  ? t("image_workbench.model.whenHint")
                  : t("image_workbench.model.whenOff")
              }}
            </span>
          </div>
          <div v-if="param.when" class="mt-2 space-y-2">
            <WhenClauseEditor
              v-for="(clause, clauseIndex) in param.when.all"
              :key="clauseIndex"
              :model-value="clause"
              :params="capability.params"
              @update:model-value="(next) => updateClause(param, clauseIndex, next)"
              @remove="removeClause(param, clauseIndex)"
            />
            <ElButton size="small" :icon="Plus" @click="addClause(param)">
              {{ t("image_workbench.model.addClause") }}
            </ElButton>
          </div>
        </div>

        <!-- 按出图方式覆盖 -->
        <div v-if="declaredModes.length > 0" class="mt-3">
          <div class="mb-1 text-xs text-(--el-text-color-secondary)">
            {{ t("image_workbench.model.overrideSection") }}
          </div>
          <div
            v-for="mode in declaredModes"
            :key="mode.key"
            class="mb-2 flex flex-wrap items-end gap-3 rounded bg-(--el-fill-color-light) p-2"
          >
            <label class="flex flex-col gap-1">
              <span class="text-xs text-(--el-text-color-secondary)">{{ mode.key }}</span>
              <ElSwitch
                :model-value="!!overrideOf(param, mode.key)"
                @update:model-value="(value) => setOverride(param, mode.key, !!value)"
              />
            </label>
            <template v-if="overrideOf(param, mode.key)">
              <label v-if="hasDomain(param)" class="flex flex-col gap-1">
                <span class="text-xs text-(--el-text-color-secondary)">
                  {{ t("image_workbench.model.allowed") }}
                </span>
                <ElInputTag
                  :model-value="toTagValues(overrideAllowed(param, mode.key))"
                  :placeholder="t('image_workbench.model.allowedPlaceholder')"
                  @update:model-value="(tags) => onOverrideAllowedChange(param, mode.key, tags)"
                />
              </label>
              <label class="flex flex-col gap-1">
                <span class="text-xs text-(--el-text-color-secondary)">
                  {{ t("image_workbench.model.defaultValue") }}
                </span>
                <ParamValueInput
                  :model-value="overrideDefault(param, mode.key)"
                  :control="param.control"
                  :allowed="overrideAllowed(param, mode.key)"
                  :allow-custom="!!param.allow_custom"
                  @update:model-value="(value) => setOverrideDefault(param, mode.key, value)"
                />
              </label>
            </template>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ArrowDown, ArrowUp, Delete, Plus } from "@element-plus/icons-vue";
import { ElMessage } from "element-plus";
import {
  createDefaultMarker,
  createDefaultMode,
  createDefaultParam,
  type CapabilityMode,
  type CapabilityModeMarker,
  type CapabilityParam,
  type CapabilityParamOverride,
  type CapabilityWhenClause,
  type ModelCapability,
  type ParamControl,
  type ParamSource,
} from "@/api/module_image_workbench/model";
import { formatValue, parseTagValues, toTagValues } from "../../values";
import ParamValueInput from "../../components/ParamValueInput.vue";
import WhenClauseEditor from "./WhenClauseEditor.vue";

defineOptions({
  name: "ImageWorkbenchCapabilityEditor",
  inheritAttrs: false,
});

const capability = defineModel<ModelCapability>({ required: true });

const { t } = useI18n();

const CONTROLS: ParamControl[] = ["select", "multiselect", "switch", "number", "text"];
const SOURCES: ParamSource[] = ["documented", "inferred", "measured"];

/** 改名前的键：`@change` 触发时 v-model 已写入新值，旧值只能靠聚焦时记下来 */
const editingParamKey = ref("");
const editingModeKey = ref("");

// ── 展示辅助 ───
function paramTitle(param: CapabilityParam, index: number) {
  return (
    param.label?.zh?.trim() ||
    param.key?.trim() ||
    t("image_workbench.model.paramIndex", { index: index + 1 })
  );
}

function sourceLabel(source?: ParamSource) {
  return t(`image_workbench.model.source_${source ?? "documented"}`);
}

function sourceTagType(source?: ParamSource) {
  // 未实测的取值必须与实测过的可辨认（spec: service-config / 能力声明的预置）
  if (source === "inferred") return "warning";
  if (source === "measured") return "success";
  return "info";
}

/** 已声明键的参数条目：出图方式的触发、条件依赖的左值都只能引用它们 */
const declaredParams = computed(() => capability.value.params.filter((param) => param.key?.trim()));
const declaredModes = computed(() => capability.value.modes.filter((mode) => mode.key?.trim()));

function paramByKey(key?: string | null) {
  return declaredParams.value.find((param) => param.key === key);
}

/** 取值域只对 select / multiselect 有意义；开关的取值域恒为是/否 */
function hasDomain(param: CapabilityParam) {
  return param.control === "select" || param.control === "multiselect";
}

function acceptsCustom(param: CapabilityParam) {
  return param.control === "select";
}

// ── 明显缺漏提示（非阻塞：真正的不合规由后端拒绝并给出具体是哪一条） ───
const problems = computed<string[]>(() => {
  const list: string[] = [];
  const seenParams = new Set<string>();
  capability.value.params.forEach((param, index) => {
    const name = paramTitle(param, index);
    const key = param.key?.trim() ?? "";
    if (!key) {
      list.push(t("image_workbench.model.problemParamKeyRequired", { name }));
    } else if (seenParams.has(key)) {
      list.push(t("image_workbench.model.problemParamKeyDuplicate", { key }));
    } else {
      seenParams.add(key);
    }
    if (!param.label?.zh?.trim() || !param.label?.en?.trim()) {
      list.push(t("image_workbench.model.problemParamLabelRequired", { name }));
    }
    if ((param.control === "switch" || hasDomain(param)) && param.default === undefined) {
      list.push(t("image_workbench.model.problemParamDefaultRequired", { name }));
    }
    if (hasDomain(param) && (param.allowed ?? []).length === 0) {
      list.push(t("image_workbench.model.problemParamAllowedRequired", { name }));
    }
    if (param.when?.all.some((clause) => !clause.param && !clause.request)) {
      list.push(t("image_workbench.model.problemClauseLeftRequired", { name }));
    }
  });

  const seenModes = new Set<string>();
  capability.value.modes.forEach((mode, index) => {
    const key = mode.key?.trim() ?? "";
    if (!key) {
      list.push(t("image_workbench.model.problemModeKeyRequired", { index: index + 1 }));
    } else if (seenModes.has(key)) {
      list.push(t("image_workbench.model.problemModeKeyDuplicate", { key }));
    } else {
      seenModes.add(key);
    }
    if (!mode.label?.zh?.trim() || !mode.label?.en?.trim()) {
      list.push(t("image_workbench.model.problemModeLabelRequired", { name: key || index + 1 }));
    }
    // 固有方式必须声明标记语法（后端同样强制）
    if (!mode.trigger) {
      const name = key || index + 1;
      if (!mode.marker) {
        list.push(t("image_workbench.model.problemModeMarkerRequired", { name }));
      } else if (!markerPlaceholdersLineUp(mode.marker)) {
        list.push(t("image_workbench.model.problemModeMarkerInvalid", { name }));
      }
    }
  });
  return list;
});

/** 占位名是否对得上：占位形态含一个 `{n}`，标记写法含 `{x1}{y1}{x2}{y2}` 各一次 */
function markerPlaceholdersLineUp(marker: CapabilityModeMarker): boolean {
  const fields = (text: string) =>
    (text.match(/\{([a-zA-Z0-9_]*)\}/g) ?? []).map((item) => item.slice(1, -1));
  const placeholder = fields(marker.placeholder ?? "");
  const template = fields(marker.template ?? "");
  return (
    placeholder.length === 1 &&
    placeholder[0] === "n" &&
    template.length === 4 &&
    ["x1", "y1", "x2", "y2"].every((name) => template.filter((item) => item === name).length === 1)
  );
}

// ── 出图方式 ───
function addMode() {
  capability.value.modes.push(createDefaultMode());
}

function removeMode(index: number) {
  const [removed] = capability.value.modes.splice(index, 1);
  if (!removed?.key) return;
  // 该方式不存在了，指向它的覆盖必须一并清掉，否则保存时被后端以「引用了不存在的出图方式」拒绝
  for (const param of capability.value.params) {
    if (param.overrides?.[removed.key]) delete param.overrides[removed.key];
  }
}

function renameModeReferences(oldKey: string, newKey: string) {
  if (!oldKey || !newKey || oldKey === newKey) return;
  for (const param of capability.value.params) {
    const overrides = param.overrides;
    if (!overrides?.[oldKey]) continue;
    overrides[newKey] = overrides[oldKey];
    delete overrides[oldKey];
  }
}

/** 触发取值在该参数形态下的合理初值 */
function initialValueForParam(param?: CapabilityParam): unknown {
  if (!param) return "";
  if (param.control === "switch") return true;
  if (param.control === "number") return 0;
  if (param.control === "select") return param.allowed?.[0] ?? "";
  if (param.control === "multiselect") return param.allowed?.length ? [param.allowed[0]] : [];
  return "";
}

function setModeTrigger(mode: CapabilityMode, enabled: boolean) {
  if (!enabled) {
    mode.trigger = null;
    // 固有方式必须带标记语法：切过来的这一刻就把默认写法备好，不让它停在「不合规」状态
    markerOf(mode);
    return;
  }
  const first = declaredParams.value[0];
  if (!first) {
    mode.trigger = null;
    markerOf(mode);
    ElMessage.warning(t("image_workbench.model.modeTriggerNeedsParam"));
    return;
  }
  mode.trigger = { param: first.key, eq: initialValueForParam(first) };
}

/** 取该方式的标记语法，缺省即补一份默认写法（固有方式在后端必填，界面上不该留空当） */
function markerOf(mode: CapabilityMode): CapabilityModeMarker {
  if (!mode.marker) mode.marker = createDefaultMarker();
  return mode.marker;
}

function setTriggerParam(mode: CapabilityMode, key: string) {
  if (!mode.trigger) return;
  // 触发参数一变，取值形态随之改变，旧触发值不再对应，重置为该形态下的初值
  mode.trigger = { param: key, eq: initialValueForParam(paramByKey(key)) };
}

function setTriggerValue(mode: CapabilityMode, value: unknown) {
  if (mode.trigger) mode.trigger.eq = value;
}

function triggerControl(mode: CapabilityMode): ParamControl {
  return paramByKey(mode.trigger?.param)?.control ?? "text";
}

function triggerAllowed(mode: CapabilityMode) {
  return paramByKey(mode.trigger?.param)?.allowed ?? [];
}

// ── 参数条目 ───
function addParam() {
  capability.value.params.push(createDefaultParam());
}

function removeParam(index: number) {
  const [removed] = capability.value.params.splice(index, 1);
  if (!removed?.key) return;
  // 参数不存在了，指向它的引用必须一并清掉：出图方式的触发与其它参数的条件依赖都会悬空
  for (const mode of capability.value.modes) {
    if (mode.trigger?.param === removed.key) mode.trigger = null;
  }
  for (const param of capability.value.params) {
    if (!param.when) continue;
    param.when.all = param.when.all.filter((clause) => clause.param !== removed.key);
    if (param.when.all.length === 0) param.when = null;
  }
}

function moveParam(index: number, offset: number) {
  const params = capability.value.params;
  const target = index + offset;
  if (target < 0 || target >= params.length) return;
  const [item] = params.splice(index, 1);
  if (item) params.splice(target, 0, item);
}

function renameParamReferences(oldKey: string, newKey: string) {
  if (!oldKey || !newKey || oldKey === newKey) return;
  for (const mode of capability.value.modes) {
    if (mode.trigger?.param === oldKey) mode.trigger.param = newKey;
  }
  for (const param of capability.value.params) {
    for (const clause of param.when?.all ?? []) {
      if (clause.param === oldKey) clause.param = newKey;
    }
  }
}

/** 取值形态变了，取值域与默认值的形态必须跟着变，否则一保存就被后端拒绝 */
function onControlChange(param: CapabilityParam) {
  param.allowed = normalizeAllowed(param.control, param.allowed);
  param.default = defaultForControl(param.control, param.default, param.allowed);
}

function normalizeAllowed(
  control: ParamControl,
  allowed: unknown[] | null | undefined
): unknown[] | undefined {
  const list = allowed ?? [];
  // 开关的取值域恒为是/否，声明 allowed 会被后端拒绝
  if (control === "switch") return undefined;
  if (control === "number") {
    const numbers = list.filter((item) => typeof item === "number");
    return numbers.length > 0 ? numbers : undefined;
  }
  return list;
}

function defaultForControl(
  control: ParamControl,
  previous: unknown,
  allowed?: unknown[] | null
): unknown {
  if (control === "switch") return typeof previous === "boolean" ? previous : false;
  if (control === "number") return typeof previous === "number" ? previous : 0;
  if (control === "multiselect") {
    if (Array.isArray(previous)) return previous;
    return allowed?.length ? [allowed[0]] : [];
  }
  if (Array.isArray(previous)) return previous[0] ?? "";
  if (previous === null || previous === undefined || typeof previous === "object") return "";
  if (control === "text") return formatValue(previous);
  return previous;
}

function onAllowedChange(param: CapabilityParam, tags?: string[]) {
  param.allowed = parseTagValues(tags, param.control);
}

// ── 条件依赖 ───
function setWhenEnabled(param: CapabilityParam, enabled: boolean) {
  if (!enabled) {
    param.when = null;
    return;
  }
  const first = declaredParams.value[0];
  param.when = {
    all: [
      first
        ? { param: first.key, eq: initialValueForParam(first) }
        : { request: "reference_count", eq: 1 },
    ],
  };
}

function addClause(param: CapabilityParam) {
  if (!param.when) return;
  const first = declaredParams.value[0];
  param.when.all.push(
    first
      ? { param: first.key, eq: initialValueForParam(first) }
      : { request: "reference_count", eq: 1 }
  );
}

/** 换左值会整体替换这一条条件（右值的类型跟着左值走），因此按索引回写 */
function updateClause(param: CapabilityParam, index: number, next: CapabilityWhenClause) {
  if (!param.when) return;
  param.when.all[index] = next;
}

function removeClause(param: CapabilityParam, index: number) {
  if (!param.when) return;
  param.when.all.splice(index, 1);
  // 空数组会被后端拒绝（无依赖时整体省略），最后一条删掉即视为不再有条件依赖
  if (param.when.all.length === 0) param.when = null;
}

// ── 按出图方式的覆盖 ───
function overrideOf(param: CapabilityParam, modeKey: string): CapabilityParamOverride | undefined {
  return param.overrides?.[modeKey];
}

function overrideAllowed(param: CapabilityParam, modeKey: string) {
  return overrideOf(param, modeKey)?.allowed ?? param.allowed ?? [];
}

function overrideDefault(param: CapabilityParam, modeKey: string) {
  return overrideOf(param, modeKey)?.default ?? param.default;
}

function setOverride(param: CapabilityParam, modeKey: string, enabled: boolean) {
  const overrides = { ...(param.overrides ?? {}) };
  if (enabled) {
    // 以条目自身的取值域与默认值起步，管理员在此之上改，而不是从空白开始
    overrides[modeKey] = {
      allowed: hasDomain(param) ? [...(param.allowed ?? [])] : undefined,
      default: param.default,
    };
  } else {
    delete overrides[modeKey];
  }
  param.overrides = overrides;
}

function onOverrideAllowedChange(param: CapabilityParam, modeKey: string, tags?: string[]) {
  const override = param.overrides?.[modeKey];
  if (override) override.allowed = parseTagValues(tags, param.control);
}

function setOverrideDefault(param: CapabilityParam, modeKey: string, value: unknown) {
  const override = param.overrides?.[modeKey];
  if (override) override.default = value;
}
</script>
