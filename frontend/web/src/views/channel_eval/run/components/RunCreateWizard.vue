<!-- 渠道评估 - 运行创建向导：选平台 → 实时拉渠道树勾模型 → 运行参数 → 创建并执行 -->
<template>
  <FaDialog
    v-model="visible"
    :title="$t('channel_eval.run.createTitle')"
    width="900px"
    dialog-class="crud-embed-dialog"
    modal-class="crud-embed-dialog"
    :close-on-click-modal="false"
    @close="visible = false"
  >
    <ElSteps :active="step" finish-status="success" simple class="mb-4">
      <ElStep :title="$t('channel_eval.run.stepPlatform')" />
      <ElStep :title="$t('channel_eval.run.stepCombos')" />
      <ElStep :title="$t('channel_eval.run.stepParams')" />
    </ElSteps>

    <!-- 步骤一：选择平台 -->
    <div v-show="step === 0">
      <ElForm label-width="90px" label-position="right">
        <ElFormItem :label="$t('channel_eval.run.platform')">
          <ElSelect
            v-model="platformId"
            :loading="platformsLoading"
            filterable
            class="w-full"
            :placeholder="$t('channel_eval.run.platformPlaceholder')"
            @change="handlePlatformChange"
          >
            <ElOption
              v-for="item in selectablePlatforms"
              :key="item.id"
              :label="item.name"
              :value="item.id!"
            >
              <span>{{ item.name }}</span>
              <span v-if="item.is_baseline" class="ml-2 text-xs text-success">
                {{ $t("channel_eval.common.isBaseline") }}
              </span>
            </ElOption>
          </ElSelect>
        </ElFormItem>
      </ElForm>
      <ElAlert
        v-if="!platformsLoading && platformLoadError"
        type="error"
        :closable="false"
        :title="platformLoadError"
      />
      <ElAlert
        v-else-if="!platformsLoading && selectablePlatforms.length === 0"
        type="warning"
        :closable="false"
        :title="$t('channel_eval.run.noEnabledPlatform')"
      />
      <div v-if="platformId && catalogLoading" class="mt-2 text-sm text-g-500">
        {{ $t("channel_eval.run.catalogLoading") }}
      </div>
      <div v-if="platformId && catalogError" class="mt-2 flex items-center gap-2">
        <ElAlert class="flex-1" type="error" :closable="false" :title="catalogError" />
        <ElButton @click="loadCatalog">{{ $t("channel_eval.run.catalogRetry") }}</ElButton>
      </div>
      <p class="mt-2 text-xs text-g-500">{{ $t("channel_eval.run.platformHint") }}</p>
    </div>

    <!-- 步骤二：渠道 × 模型 -->
    <div
      v-show="step === 1"
      v-loading="catalogLoading"
      :element-loading-text="$t('channel_eval.run.catalogLoading')"
    >
      <template v-if="catalog">
        <div class="mb-2 flex flex-wrap items-center justify-between gap-2">
          <span class="text-sm text-g-600">
            {{ $t("channel_eval.run.selectedSummary", { count: comboCount }) }}
          </span>
          <ElInput
            v-model="modelFilter"
            clearable
            size="small"
            class="w-56"
            :placeholder="$t('channel_eval.run.modelFilterPlaceholder')"
          />
        </div>
        <ElScrollbar max-height="52vh">
          <div
            v-for="channel in filteredChannels"
            :key="channel.channel_id"
            class="mb-3 rounded border border-g-200 p-3"
          >
            <div class="mb-2 flex flex-wrap items-center justify-between gap-2">
              <div class="flex flex-wrap items-center gap-2">
                <span class="text-sm font-medium">
                  #{{ channel.channel_id }} {{ channel.name }}
                </span>
                <ElTag size="small" :type="channel.enabled ? 'success' : 'info'">
                  {{ channelStatusLabel(channel) }}
                </ElTag>
                <ElTooltip
                  v-if="channel.is_standby"
                  :content="channel.standby_hint || ''"
                  placement="top"
                >
                  <ElTag size="small" type="warning">
                    {{ $t("channel_eval.run.standbyTag") }}
                  </ElTag>
                </ElTooltip>
                <span class="text-xs text-g-500">
                  {{ $t("channel_eval.run.priority") }} {{ channel.priority }} ·
                  {{ $t("channel_eval.run.weight") }} {{ channel.weight }}
                  <template v-if="channel.groups.length">
                    · {{ $t("channel_eval.run.groups") }} {{ channel.groups.join(", ") }}
                  </template>
                </span>
              </div>
              <div class="flex items-center">
                <ElButton link size="small" @click="selectAllModels(channel)">
                  {{ $t("channel_eval.run.selectAll") }}
                </ElButton>
                <ElButton link size="small" @click="clearModels(channel)">
                  {{ $t("channel_eval.run.clearAll") }}
                </ElButton>
              </div>
            </div>
            <ElCheckboxGroup v-model="selection[channel.channel_id]">
              <ElCheckbox v-for="model in filteredModels(channel)" :key="model" :value="model">
                {{ model }}
              </ElCheckbox>
            </ElCheckboxGroup>
            <div v-if="filteredModels(channel).length === 0" class="text-xs text-g-400">
              {{ $t("channel_eval.run.noModelMatch") }}
            </div>
          </div>
        </ElScrollbar>
        <ElAlert
          v-if="comboCount === 0"
          class="mt-2"
          type="info"
          :closable="false"
          :title="$t('channel_eval.run.emptyCombos')"
        />
      </template>
      <ElEmpty v-else-if="!catalogLoading" :description="$t('channel_eval.run.catalogFailed')" />
    </div>

    <!-- 步骤三：运行参数 -->
    <div v-show="step === 2">
      <p class="mb-2 text-xs text-g-500">{{ $t("channel_eval.run.paramsTitle") }}</p>
      <FaForm
        ref="paramsFormRef"
        v-model="params"
        :items="paramItems"
        :rules="paramRules"
        label-suffix=":"
        :label-width="150"
        label-position="right"
        :span="24"
        :show-reset="false"
        :show-submit="false"
      >
        <template #warmup>
          <div class="flex flex-wrap items-center gap-2">
            <ElSwitch v-model="params.warmup" />
            <span class="text-xs text-g-500">{{ $t("channel_eval.run.warmupHint") }}</span>
          </div>
        </template>
      </FaForm>
    </div>

    <template #footer>
      <ElButton @click="visible = false">{{ $t("common.cancel") }}</ElButton>
      <ElButton v-if="step > 0" @click="step -= 1">
        {{ $t("channel_eval.run.prevStep") }}
      </ElButton>
      <ElButton v-if="step < 2" type="primary" :disabled="!canGoNext" @click="goNext">
        {{ $t("channel_eval.run.nextStep") }}
      </ElButton>
      <ElButton v-else type="primary" :loading="submitting" @click="submit">
        {{ $t("channel_eval.run.submit") }}
      </ElButton>
    </template>
  </FaDialog>
</template>

<script setup lang="ts">
import type { ChannelCatalog, ChannelCatalogItem } from "@/api/module_channel_eval/platform";
import RunAPI, {
  type RunComboInput,
  type RunPlatformOption,
  type RunTable,
} from "@/api/module_channel_eval/run";
import { ElMessage } from "element-plus";
import type { FormItem } from "@/components/forms/fa-form/index.vue";
import FaForm from "@/components/forms/fa-form/index.vue";

defineOptions({ name: "RunCreateWizard" });

const visible = defineModel<boolean>({ default: false });
const emit = defineEmits<{ created: [run: RunTable] }>();

const { t } = useI18n();

const step = ref(0);

// ─── 平台列表 ───
const platformsLoading = ref(false);
const platforms = ref<RunPlatformOption[]>([]);
const platformLoadError = ref<string | null>(null);
const selectablePlatforms = computed(() => platforms.value.filter((item) => item.status === 0));
const platformId = ref<number | undefined>(undefined);

// ── 渠道目录（实时拉取，不缓存） ───
const catalog = ref<ChannelCatalog | null>(null);
const catalogLoading = ref(false);
const catalogError = ref<string | null>(null);

const selection = reactive<Record<number, string[]>>({});
const modelFilter = ref("");

const comboCount = computed(() =>
  Object.values(selection).reduce((sum, models) => sum + models.length, 0)
);

const filteredChannels = computed(() => {
  if (!catalog.value) return [];
  const keyword = modelFilter.value.trim().toLowerCase();
  if (!keyword) return catalog.value.channels;
  return catalog.value.channels.filter((channel) =>
    channel.models.some((model) => model.toLowerCase().includes(keyword))
  );
});

function filteredModels(channel: ChannelCatalogItem) {
  const keyword = modelFilter.value.trim().toLowerCase();
  if (!keyword) return channel.models;
  return channel.models.filter((model) => model.toLowerCase().includes(keyword));
}

function channelStatusLabel(channel: ChannelCatalogItem) {
  if (channel.enabled) return t("channel_eval.run.channelEnabled");
  return channel.status === 3
    ? t("channel_eval.run.channelDisabledAuto")
    : t("channel_eval.run.channelDisabledManual");
}

function selectAllModels(channel: ChannelCatalogItem) {
  selection[channel.channel_id] = [...filteredModels(channel)];
}

function clearModels(channel: ChannelCatalogItem) {
  selection[channel.channel_id] = [];
}

// ── 运行参数 ──
const DEFAULT_PROMPT = "请用一段话介绍你自己，并数到 5。";

function buildDefaultParams() {
  return {
    request_count: 3,
    warmup: true,
    interval_seconds: 2,
    timeout_seconds: 300,
    prompt: DEFAULT_PROMPT,
    anomaly_threshold_seconds: 30,
  };
}

const params = ref(buildDefaultParams());
const paramsFormRef = ref<InstanceType<typeof FaForm> | null>(null);
const submitting = ref(false);

const paramItems = computed<FormItem[]>(() => [
  {
    label: t("channel_eval.run.requestCount"),
    key: "request_count",
    type: "number",
    span: 24,
    props: { min: 1, max: 50, step: 1, class: "w-full max-w-xs" },
  },
  { label: t("channel_eval.run.warmup"), key: "warmup", type: "switch", span: 24 },
  {
    label: t("channel_eval.run.intervalSeconds"),
    key: "interval_seconds",
    type: "number",
    span: 24,
    props: { min: 0, max: 60, step: 0.5, class: "w-full max-w-xs" },
  },
  {
    label: t("channel_eval.run.timeoutSeconds"),
    key: "timeout_seconds",
    type: "number",
    span: 24,
    props: { min: 5, max: 1800, step: 5, class: "w-full max-w-xs" },
  },
  {
    label: t("channel_eval.run.anomalyThreshold"),
    key: "anomaly_threshold_seconds",
    type: "number",
    span: 24,
    props: { min: 1, max: 600, step: 1, class: "w-full max-w-xs" },
  },
  {
    label: t("channel_eval.run.prompt"),
    key: "prompt",
    type: "input",
    span: 24,
    props: { type: "textarea", rows: 3, maxlength: 4000, showWordLimit: true },
  },
]);

const paramRules = computed(() => ({
  request_count: [
    { required: true, message: t("channel_eval.run.requestCountRequired"), trigger: "change" },
  ],
  timeout_seconds: [
    { required: true, message: t("channel_eval.run.timeoutRequired"), trigger: "change" },
  ],
  anomaly_threshold_seconds: [
    { required: true, message: t("channel_eval.run.anomalyRequired"), trigger: "change" },
  ],
  prompt: [{ required: true, message: t("channel_eval.run.promptRequired"), trigger: "blur" }],
}));

// ─── 流程控制 ───
const canGoNext = computed(() => {
  if (step.value === 0) {
    return Boolean(platformId.value) && Boolean(catalog.value) && !catalogLoading.value;
  }
  if (step.value === 1) return comboCount.value > 0;
  return true;
});

function extractErrorMessage(error: unknown, fallback: string) {
  const maybe = error as { data?: { msg?: string }; message?: string };
  return maybe?.data?.msg || maybe?.message || fallback;
}

async function loadCatalog() {
  if (!platformId.value) return;
  catalogLoading.value = true;
  catalogError.value = null;
  try {
    const response = await RunAPI.getPlatformChannels(platformId.value);
    const data = response.data.data;
    catalog.value = data;
    Object.keys(selection).forEach((key) => delete selection[Number(key)]);
    for (const channel of data.channels) {
      selection[channel.channel_id] = [];
    }
  } catch (error) {
    catalog.value = null;
    catalogError.value = extractErrorMessage(error, t("channel_eval.run.catalogFailed"));
  } finally {
    catalogLoading.value = false;
  }
}

function handlePlatformChange() {
  catalog.value = null;
  catalogError.value = null;
  modelFilter.value = "";
  Object.keys(selection).forEach((key) => delete selection[Number(key)]);
  void loadCatalog();
}

async function goNext() {
  step.value += 1;
}

function resetWizard() {
  step.value = 0;
  platformId.value = undefined;
  catalog.value = null;
  catalogError.value = null;
  catalogLoading.value = false;
  modelFilter.value = "";
  Object.keys(selection).forEach((key) => delete selection[Number(key)]);
  params.value = buildDefaultParams();
  submitting.value = false;
}

watch(visible, async (value) => {
  if (!value) return;
  resetWizard();
  platformsLoading.value = true;
  platformLoadError.value = null;
  try {
    const response = await RunAPI.platformOptions();
    platforms.value = response.data.data ?? [];
  } catch (error) {
    platforms.value = [];
    platformLoadError.value = extractErrorMessage(error, t("channel_eval.run.platformLoadFailed"));
  } finally {
    platformsLoading.value = false;
  }
});

async function submit() {
  const form = paramsFormRef.value;
  const valid = form ? await (form.validate as () => Promise<boolean>)().catch(() => false) : false;
  if (!valid) return;
  if (comboCount.value === 0) {
    ElMessage.warning(t("channel_eval.run.combosRequired"));
    step.value = 1;
    return;
  }
  submitting.value = true;
  try {
    const combos: RunComboInput[] = [];
    for (const channel of catalog.value?.channels ?? []) {
      for (const model of selection[channel.channel_id] ?? []) {
        combos.push({ channel_id: channel.channel_id, channel_name: channel.name, model });
      }
    }
    const response = await RunAPI.createRun({
      platform_id: platformId.value!,
      combos,
      ...params.value,
    });
    visible.value = false;
    emit("created", response.data.data);
  } catch {
    // 请求失败已由拦截器提示
  } finally {
    submitting.value = false;
  }
}
</script>
