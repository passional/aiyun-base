<!--
  绘图工作台 - 工作台：左侧生成表单（按所选模型的能力声明逐条渲染）+ 右侧历史列表与详情。

  出图参数**没有一个是写死的**（spec: service-config / 绘图模型能力声明）：表单按清单逐条
  渲染，可用性、取值范围与默认值都来自求值结果（见 capability.ts，与后端同一套规则）。
-->
<template>
  <div class="fa-full-height">
    <ElSplitter :style="'height: 100%'">
      <!-- 左：生成 -->
      <ElSplitterPanel size="36%" :min="400">
        <div class="flex flex-col h-full overflow-hidden pr-2">
          <ElCard class="fa-table-card" shadow="never">
            <template #header>
              <div class="flex flex-wrap items-center justify-between gap-2">
                <span class="font-semibold">{{ t("image_workbench.job.generateTitle") }}</span>
                <span v-if="trackedJob" class="inline-flex items-center gap-1.5 text-xs">
                  <i
                    class="h-2 w-2 rounded-full"
                    :class="streamConnected ? 'bg-(--el-color-success)' : 'bg-(--el-color-info)'"
                  />
                  {{
                    streamConnected
                      ? t("image_workbench.job.streamConnected")
                      : t("image_workbench.job.streamConnecting")
                  }}
                </span>
              </div>
            </template>

            <ElEmpty
              v-if="modelOptions.length === 0"
              :description="t('image_workbench.job.noModelHint')"
            />

            <template v-else>
              <FaForm
                ref="generateFormRef"
                v-model="generateForm"
                :items="generateFormItems"
                :rules="generateRules"
                label-suffix=":"
                :label-width="96"
                label-position="right"
                :span="24"
                :gutter="12"
                :show-reset="false"
                :show-submit="false"
                :disabled="submitting"
              >
                <!-- 提示词：用自定义输入框是为了能拿到光标位置——占位符要插在用户当前落笔的地方 -->
                <template #prompt>
                  <ElInput
                    ref="promptInputRef"
                    v-model="generateForm.prompt"
                    type="textarea"
                    :rows="4"
                    :maxlength="4000"
                    show-word-limit
                    :placeholder="t('image_workbench.job.promptPlaceholder')"
                    :disabled="submitting"
                  />
                </template>

                <template #reference_images>
                  <div class="w-full">
                    <div v-if="!allowReference" class="text-xs text-(--el-text-color-info)">
                      {{ t("image_workbench.job.referenceNotSupported") }}
                    </div>
                    <template v-else>
                      <div class="flex flex-wrap items-center gap-2">
                        <ElUpload
                          ref="referenceUploadRef"
                          :auto-upload="false"
                          :show-file-list="false"
                          :multiple="true"
                          accept="image/*"
                          :on-change="handleReferencePick"
                        >
                          <ElButton :icon="Plus" :disabled="referenceUploading">
                            {{ t("image_workbench.job.referenceUpload") }}
                          </ElButton>
                        </ElUpload>
                        <span class="text-xs text-(--el-text-color-secondary)">
                          {{
                            t("image_workbench.job.referenceHint", {
                              current: referenceImages.length,
                              count: maxReferenceImages,
                            })
                          }}
                        </span>
                      </div>
                      <div v-if="referenceImages.length > 0" class="mt-2 flex flex-wrap gap-2">
                        <div
                          v-for="(item, idx) in referenceImages"
                          :key="item.path"
                          class="relative h-16 w-16 overflow-hidden rounded border border-(--el-border-color)"
                        >
                          <img
                            :src="staticUrl(item.path)"
                            class="h-full w-full object-cover"
                            alt=""
                          />
                          <ElButton
                            class="absolute top-0 right-0"
                            size="small"
                            type="danger"
                            circle
                            :icon="Close"
                            @click="removeReferenceImage(idx)"
                          />
                        </div>
                      </div>
                    </template>
                  </div>
                </template>
              </FaForm>

              <!-- 画布入口：所选模型的声明含交互编辑时才出现（design D8） -->
              <div v-if="coordinateMarker" class="mt-3 flex flex-wrap items-center gap-2 px-4">
                <ElButton
                  v-hasPerm="['image_workbench:job:create']"
                  :icon="Brush"
                  :disabled="submitting"
                  @click="openCanvasFromForm"
                >
                  {{ t("image_workbench.canvas.open") }}
                </ElButton>
                <span class="text-xs text-(--el-text-color-secondary)">
                  {{ t("image_workbench.canvas.entryHint") }}
                </span>
              </div>

              <!-- 已指定的位置：序号与提示词里的区域占位符一一对应 -->
              <div v-if="selections.length > 0" class="mt-2 px-4">
                <div
                  v-for="item in selections"
                  :key="item.index"
                  class="mb-1 flex items-center gap-2 text-xs"
                >
                  <ElTag size="small" type="success">
                    {{
                      t("image_workbench.canvas.selectionTag", {
                        index: item.index,
                        bbox: selectionBboxText(item),
                      })
                    }}
                  </ElTag>
                  <span class="text-(--el-text-color-secondary)">
                    {{
                      item.artifact_id
                        ? t("image_workbench.canvas.fromLayer", { id: item.artifact_id })
                        : t("image_workbench.canvas.fromDraw")
                    }}
                  </span>
                  <ElButton link size="small" @click="insertPlaceholder(item.index)">
                    {{ t("image_workbench.canvas.insertAgain") }}
                  </ElButton>
                  <ElButton link type="danger" size="small" @click="removeSelection(item.index)">
                    {{ t("image_workbench.common.delete") }}
                  </ElButton>
                </div>
              </div>

              <!-- 出图参数：按能力声明逐条渲染，当前条件下不可用的置灰并说明原因 -->
              <div v-if="resolvedParams.length > 0" class="mt-3 px-4">
                <div
                  v-for="param in resolvedParams"
                  :key="param.key"
                  class="mb-3 flex items-start gap-0"
                >
                  <span class="w-24 shrink-0 pt-1 text-right text-sm">
                    {{ labelText(param.param.label) }}:
                  </span>
                  <div class="min-w-0 flex-1 pl-3">
                    <ParamValueInput
                      :model-value="paramValues[param.key]"
                      :control="param.control"
                      :allowed="param.allowed"
                      :allow-custom="param.allowCustom"
                      :disabled="!param.available || submitting"
                      @update:model-value="(value) => onParamChange(param.key, value)"
                    />
                    <div
                      v-if="!param.available"
                      class="mt-1 text-xs text-(--el-text-color-secondary)"
                    >
                      {{ unavailableReason(param) }}
                    </div>
                  </div>
                </div>
              </div>

              <div class="mt-3 flex items-center gap-2">
                <ElButton
                  v-hasPerm="['image_workbench:job:create']"
                  type="primary"
                  :loading="submitting"
                  @click="handleGenerate"
                >
                  {{ t("image_workbench.job.generate") }}
                </ElButton>
                <ElButton :disabled="submitting" @click="resetGenerateForm">
                  {{ t("image_workbench.common.reset") }}
                </ElButton>
              </div>
              <div v-if="progressMessage" class="mt-2 text-xs text-(--el-text-color-secondary)">
                {{ progressMessage }}
              </div>
            </template>
          </ElCard>
        </div>
      </ElSplitterPanel>

      <!-- 右：历史记录 -->
      <ElSplitterPanel :min="420">
        <div class="flex flex-col h-full overflow-hidden pl-2">
          <FaSearchBar
            v-show="showSearchBar"
            ref="searchBarRef"
            v-model="searchForm"
            :items="searchItems"
            :rules="searchBarRules"
            :is-expand="false"
            :show-expand="true"
            :show-reset="true"
            :show-search="true"
            :disabled-search="false"
            :default-expanded="false"
            @search="handleSearchBarSearch"
            @reset="onResetSearch"
          />

          <ElCard class="fa-table-card" :style="{ 'margin-top': showSearchBar ? '12px' : '0' }">
            <FaTableHeader
              v-model:columns="columnChecks"
              v-model:showSearchBar="showSearchBar"
              :loading="loading"
              @refresh="refreshData"
            >
              <template #left>
                <FaTableHeaderLeft :remove-ids="selectedIds">
                  <ElButton
                    v-hasPerm="['image_workbench:job:delete']"
                    type="danger"
                    plain
                    :icon="Delete"
                    :loading="batchDeleting"
                    :disabled="selectedIds.length === 0"
                    @click="handleBatchDelete"
                  >
                    {{
                      selectedIds.length > 0
                        ? `${t("image_workbench.common.batchDelete")} (${selectedIds.length})`
                        : t("image_workbench.common.batchDelete")
                    }}
                  </ElButton>
                </FaTableHeaderLeft>
              </template>
            </FaTableHeader>

            <FaTable
              ref="faTableRef"
              :loading="loading"
              :data="data"
              :columns="columns"
              :pagination="pagination"
              @selection-change="onTableSelectionChange"
              @pagination:size-change="handleSizeChange"
              @pagination:current-change="handleCurrentChange"
            >
              <template #prompt="{ row }">
                <span class="line-clamp-2">{{ row.prompt }}</span>
              </template>
              <template #status="{ row }">
                <FaStatusTag
                  :type="jobStatusTagType(row.status)"
                  :label="jobStatusLabel(row.status)"
                />
              </template>
              <template #thumbnail="{ row }">
                <ElImage
                  v-if="row.thumbnail_url"
                  :src="row.thumbnail_url"
                  fit="cover"
                  class="h-10 w-10 rounded"
                  :preview-src-list="[row.thumbnail_url]"
                  preview-teleported
                />
                <span v-else class="text-xs text-(--el-text-color-secondary)">—</span>
              </template>
            </FaTable>
          </ElCard>
        </div>
      </ElSplitterPanel>
    </ElSplitter>

    <!-- 画布：在参考图或图层合成画面上指定要修改的位置 -->
    <FaDialog
      v-model="canvasVisible"
      :title="t('image_workbench.canvas.title')"
      width="820px"
      dialog-class="crud-embed-dialog"
      modal-class="crud-embed-dialog"
      form-mode="detail"
      @close="handleCloseCanvas"
      @cancel="handleCloseCanvas"
    >
      <EditCanvas
        v-if="coordinateMarker"
        :artifacts="canvasArtifacts"
        :job-id="canvasJobId"
        :reference-path="canvasReferencePath"
        :range="coordinateMarker.range"
        :selections="selections"
        @pick="onCanvasPick"
        @composed="onCanvasComposed"
      />
      <div class="mt-3 text-xs text-(--el-text-color-secondary)">
        {{ t("image_workbench.canvas.dialogHint") }}
      </div>
    </FaDialog>

    <!-- 任务详情 -->
    <FaDialog
      v-model="detailVisible"
      :title="t('image_workbench.job.detailTitle')"
      width="860px"
      dialog-class="crud-embed-dialog"
      modal-class="crud-embed-dialog"
      form-mode="detail"
      @close="handleCloseDetail"
      @cancel="handleCloseDetail"
    >
      <FaDescriptions
        :column="2"
        :data="detailJob ?? {}"
        :items="detailItems"
        label-width="110px"
        max-height="70vh"
      >
        <!-- 两个提示词形态：原话（用户写的）与实际发给上游的那段；相同时只呈现一份 -->
        <template #prompt="{ row }">
          <div class="space-y-1">
            <div>
              <div class="text-xs text-(--el-text-color-secondary)">
                {{ t("image_workbench.job.promptOriginal") }}
              </div>
              <div class="whitespace-pre-wrap break-all">{{ asJob(row)?.prompt || "—" }}</div>
            </div>
            <div v-if="hasDistinctFinalPrompt(asJob(row))">
              <div class="text-xs text-(--el-text-color-secondary)">
                {{ t("image_workbench.job.promptFinal") }}
              </div>
              <div class="whitespace-pre-wrap break-all">
                {{ asJob(row)?.prompt_final }}
              </div>
            </div>
          </div>
        </template>
        <template #selections="{ row }">
          <div v-if="(asJob(row)?.selections ?? []).length > 0" class="flex flex-wrap gap-1">
            <ElTag v-for="item in asJob(row)?.selections" :key="item.index" size="small">
              {{
                t("image_workbench.canvas.selectionSource", {
                  index: item.index,
                  source: item.artifact_id
                    ? t("image_workbench.canvas.fromLayer", { id: item.artifact_id })
                    : t("image_workbench.canvas.fromDraw"),
                })
              }}
            </ElTag>
          </div>
          <span v-else class="text-xs text-(--el-text-color-secondary)">
            {{ t("image_workbench.job.noSelection") }}
          </span>
        </template>
        <template #status="{ row }">
          <FaStatusTag
            :type="jobStatusTagType(asJob(row)?.status)"
            :label="jobStatusLabel(asJob(row)?.status)"
          />
        </template>
        <template #params="{ row }">
          <div class="flex flex-wrap gap-1">
            <ElTag
              v-for="(value, key) in asJob(row)?.params ?? {}"
              :key="key"
              size="small"
              class="max-w-full"
            >
              {{ paramLabelText(String(key)) }}：{{ renderValue(value) }}
            </ElTag>
            <span
              v-if="Object.keys(asJob(row)?.params ?? {}).length === 0"
              class="text-xs text-(--el-text-color-secondary)"
            >
              —
            </span>
          </div>
        </template>
        <template #reference_images="{ row }">
          <div v-if="referenceListOf(asJob(row)).length > 0" class="flex flex-wrap gap-2">
            <img
              v-for="item in referenceListOf(asJob(row))"
              :key="item"
              :src="staticUrl(item)"
              class="h-12 w-12 rounded border border-(--el-border-color) object-cover"
              alt=""
            />
          </div>
          <span v-else class="text-xs text-(--el-text-color-secondary)">—</span>
        </template>
        <template #usage="{ row }">
          <span>{{ usageText(asJob(row)) }}</span>
        </template>
        <template #artifacts="{ row }">
          <JobArtifacts :job-id="asJob(row)?.id" :artifacts="asJob(row)?.artifacts" />
        </template>
      </FaDescriptions>

      <!-- 「拆 → 改」的接续处：拿这次生成的产物当画面，在上面指定要改的位置 -->
      <div
        v-if="canContinueEditing(detailJob)"
        class="mt-3 flex flex-wrap items-center gap-2 border-t border-(--el-border-color) pt-3"
      >
        <ElButton
          v-hasPerm="['image_workbench:job:create']"
          type="primary"
          :icon="Brush"
          @click="continueEditing(detailJob)"
        >
          {{ t("image_workbench.canvas.continueEditing") }}
        </ElButton>
        <span class="text-xs text-(--el-text-color-secondary)">
          {{ t("image_workbench.canvas.continueHint") }}
        </span>
      </div>
    </FaDialog>
  </div>
</template>

<script setup lang="ts">
import JobAPI, {
  JobStream,
  type JobArtifact,
  type JobModelOption,
  type JobParams,
  type JobProgress,
  type JobPushMessage,
  type JobSelectionInput,
  type JobStatus,
  type JobTable,
} from "@/api/module_image_workbench/job";
import {
  findCoordinateMarker,
  type CapabilityLabel,
  type CapabilityModeMarker,
} from "@/api/module_image_workbench/model";
import { renderTableOperationCell, resolveStatusColumns, type TableOperationAction } from "@utils";
import { getColumnKey } from "@/hooks/core/useTableColumns";
import { Plus, Close, Delete, Brush } from "@element-plus/icons-vue";
import { saveAs } from "file-saver";
import type { ColumnOption } from "@/types/component";
import type { SearchFormItem } from "@/components/forms/fa-search-bar/index.vue";
import type { FormItem } from "@/components/forms/fa-form/index.vue";
import type { DescriptionsItem } from "@/components/display/fa-descriptions/index.vue";
import type { UploadFile, UploadInstance } from "element-plus";
import { ElMessage, ElMessageBox } from "element-plus";
import FaSearchBar from "@/components/forms/fa-search-bar/index.vue";
import FaForm from "@/components/forms/fa-form/index.vue";
import FaDescriptions from "@/components/display/fa-descriptions/index.vue";
import FaTableHeader from "@/components/tables/fa-table-header/index.vue";
import FaTableHeaderLeft from "@/components/tables/fa-table-header-left/index.vue";
import ParamValueInput from "../components/ParamValueInput.vue";
import JobArtifacts from "./components/JobArtifacts.vue";
import EditCanvas from "./components/EditCanvas.vue";
import { staticUrl } from "../values";
import {
  acceptsValue,
  buildFacts,
  emptyFacts,
  evaluateCapability,
  type CapabilityEvaluation,
  type ResolvedParam,
  type WhenClauseView,
} from "./capability";

// 与菜单 route_name 一致：KeepAlive 的 include / exclude 按组件 name 匹配
defineOptions({
  name: "ImageWorkbenchJob",
  inheritAttrs: false,
});

const { t, locale } = useI18n();

// ── 状态展示 ───
const ACTIVE_STATUSES: JobStatus[] = ["pending", "running"];
const TERMINAL_STATUSES: JobStatus[] = ["success", "failed", "cancelled"];

const JOB_STATUS_I18N: Record<JobStatus, string> = {
  pending: "statusPending",
  running: "statusRunning",
  success: "statusSuccess",
  failed: "statusFailed",
  cancelled: "statusCancelled",
};

function isActiveStatus(status?: JobStatus | null) {
  return !!status && ACTIVE_STATUSES.includes(status);
}

function isTerminalStatus(status?: JobStatus | null) {
  return !!status && TERMINAL_STATUSES.includes(status);
}

function jobStatusLabel(status?: JobStatus | null) {
  if (!status) return "—";
  const key = JOB_STATUS_I18N[status];
  return key ? t(`image_workbench.job.${key}`) : status;
}

function jobStatusTagType(status?: JobStatus | null) {
  if (status === "success") return "success";
  if (status === "failed") return "danger";
  if (status === "cancelled") return "info";
  if (status === "running") return "warning";
  return "primary";
}

function formatElapsed(ms?: number | null) {
  if (ms == null) return "—";
  return `${(ms / 1000).toFixed(1)}s`;
}

function asJob(row: unknown): JobTable | null {
  return (row as JobTable | null) ?? null;
}

function referenceListOf(row?: JobTable | null) {
  return row?.reference_images ?? [];
}

function usageText(row?: JobTable | null) {
  const usage = row?.usage;
  if (!usage) return "—";
  const images = usage.images ?? 0;
  const upstream = usage.upstream ? JSON.stringify(usage.upstream) : "—";
  return `${t("image_workbench.job.usageImages")}: ${images}；${t("image_workbench.job.usageUpstream")}: ${upstream}`;
}

/** 参数键 → 显示名：先看**所选模型**的清单，再看列表里其它模型（历史任务的模型可能已停用） */
function paramLabelText(key: string) {
  const selected = capability.value?.params?.find((item) => item.key === key);
  if (selected) return labelText(selected.label) || key;
  for (const option of modelOptions.value) {
    const param = option.capability?.params?.find((item) => item.key === key);
    if (param) return labelText(param.label) || key;
  }
  return key;
}

/** 当前所选模型下的参数显示名（不可用原因里的左值用） */
function capabilityParamLabel(key: string) {
  const param = capability.value?.params?.find((item) => item.key === key);
  return param ? labelText(param.label) || key : key;
}

function labelText(label?: CapabilityLabel | null) {
  if (!label) return "";
  return locale.value.startsWith("zh") ? label.zh || label.en : label.en || label.zh;
}

/** 取值 → 界面短文案（布尔按语言呈现，其余如实呈现） */
function renderValue(value: unknown): string {
  if (typeof value === "boolean") {
    return value ? t("image_workbench.common.yes") : t("image_workbench.common.no");
  }
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

/** 参数快照 → 列表页的一行摘要 */
function summariseParams(params?: JobParams | null) {
  const entries = Object.entries(params ?? {});
  if (entries.length === 0) return "—";
  return entries
    .slice(0, 3)
    .map(([key, value]) => `${paramLabelText(key)}：${renderValue(value)}`)
    .join(" · ");
}

// ── 生成表单 ───
type GenerateForm = {
  model_pk?: number;
  prompt: string;
};

/** 已上传的参考图：路径 + 上传时判出的透明通道（design D9） */
interface ReferenceImage {
  path: string;
  hasAlpha: boolean;
}

const generateFormRef = ref<InstanceType<typeof FaForm> | null>(null);
const generateForm = ref<GenerateForm>({ model_pk: undefined, prompt: "" });

const modelOptions = ref<JobModelOption[]>([]);
const modelLoading = ref(false);
const submitting = ref(false);
const referenceImages = ref<ReferenceImage[]>([]);
const referenceUploading = ref(false);
const referenceUploadRef = ref<UploadInstance | null>(null);

const selectedModel = computed(
  () => modelOptions.value.find((item) => item.id === generateForm.value.model_pk) ?? null
);
const capability = computed(() => selectedModel.value?.capability ?? null);
const allowReference = computed(() => !!capability.value?.reference?.supported);
const maxReferenceImages = computed(() => capability.value?.reference?.max_images ?? 0);

const modelSelectOptions = computed(() =>
  modelOptions.value.map((item) => ({
    label: `${item.display_name}（${item.service_name}）`,
    value: item.id,
  }))
);

const generateFormItems = computed<FormItem[]>(() => [
  {
    label: t("image_workbench.job.model"),
    key: "model_pk",
    type: "select",
    span: 24,
    props: {
      placeholder: t("image_workbench.job.modelPlaceholder"),
      options: modelSelectOptions.value,
      filterable: true,
      loading: modelLoading.value,
    },
  },
  // 提示词由 `#prompt` 插槽渲染自定义输入框（要拿光标位置插入占位符），此处只留标签与校验
  { label: t("image_workbench.job.prompt"), key: "prompt", type: "input", span: 24 },
  { label: t("image_workbench.job.referenceImages"), key: "reference_images", span: 24 },
]);

type RuleCallback = (error?: Error) => void;

/** 参考图数量上限由能力声明收敛（后端同样强制） */
function referenceValidator(_rule: unknown, _value: unknown, callback: RuleCallback) {
  if (!allowReference.value && referenceImages.value.length > 0) {
    callback(new Error(t("image_workbench.job.referenceNotSupported")));
    return;
  }
  if (referenceImages.value.length > maxReferenceImages.value) {
    callback(
      new Error(t("image_workbench.job.referenceLimit", { count: maxReferenceImages.value }))
    );
    return;
  }
  callback();
}

const generateRules = computed(() => ({
  model_pk: [
    { required: true, message: t("image_workbench.job.modelRequired"), trigger: "change" },
  ],
  prompt: [{ required: true, message: t("image_workbench.job.promptRequired"), trigger: "blur" }],
  reference_images: [{ validator: referenceValidator, trigger: "change" }],
}));

// ── 出图参数：求值 + 取值同步（available / 取值域 / 默认值全来自清单） ───
const evaluation = ref<CapabilityEvaluation>({ params: [], modes: [], activeMode: null });
/** 当前表单上的取值：可用的参数都在，值是「用户改动过的」或「清单默认值」 */
const paramValues = ref<Record<string, unknown>>({});
/** 用户改动过的参数键：**只有这些**会随请求提交，其余由服务端按清单补默认值（design D5） */
const dirtyKeys = ref<Set<string>>(new Set());

const resolvedParams = computed(() => evaluation.value.params);

const facts = computed(() => {
  if (!capability.value) return emptyFacts();
  return buildFacts(referenceImages.value.map((item) => ({ hasAlpha: item.hasAlpha })));
});

function pickDirty(): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  for (const key of dirtyKeys.value) {
    const value = paramValues.value[key];
    if (value !== undefined) result[key] = value;
  }
  return result;
}

/**
 * 按当前取值与请求事实重新求值，并同步表单取值。
 *
 * 三个必须一起做对的地方：
 * 1. 求值用的「已填参数」只有用户改动过的键——与提交给后端的 `params` 同一份口径；
 * 2. 当前条件下**不可用**的参数从表单取值里移除、并取消其改动标记（它 MUST NOT 被
 *    提交、MUST NOT 被补默认值，见 design D5 的关键约束）；
 * 3. 可用的参数若用户没改过，显示清单默认值——出图方式一变，覆盖后的默认值立刻生效。
 */
function syncParams() {
  const cap = capability.value;
  if (!cap) {
    evaluation.value = { params: [], modes: [], activeMode: null };
    paramValues.value = {};
    dirtyKeys.value = new Set();
    return;
  }
  const submitted = pickDirty();
  const result = evaluateCapability(cap, { values: submitted, facts: facts.value });
  const next: Record<string, unknown> = {};
  for (const resolved of result.params) {
    if (!resolved.available) {
      dirtyKeys.value.delete(resolved.key);
      continue;
    }
    const current = submitted[resolved.key];
    const kept =
      current !== undefined &&
      acceptsValue(resolved.control, resolved.allowed, resolved.allowCustom, current);
    // 用户改过的值若已落在本次生效的取值域之外（例如切了出图方式），回到清单默认值
    if (current !== undefined && !kept) dirtyKeys.value.delete(resolved.key);
    next[resolved.key] = kept ? current : resolved.defaultValue;
  }
  paramValues.value = next;
  evaluation.value = result;
}

function onParamChange(key: string, value: unknown) {
  // 清空即「回到清单默认值」：不留改动标记，也就不随请求提交
  if (value === undefined || value === null) {
    dirtyKeys.value.delete(key);
  } else {
    dirtyKeys.value.add(key);
  }
  paramValues.value = { ...paramValues.value, [key]: value };
  syncParams();
}

/** 参数为何不可用：列出**当前不满足**的那几条条件（与后端同一套措辞） */
function unavailableReason(param: ResolvedParam) {
  const conditions = param.unsatisfied
    .map((clause) => clauseText(clause))
    .join(` ${t("image_workbench.model.whenJoin")} `);
  return t("image_workbench.job.paramUnavailable", { conditions });
}

function clauseText(clause: WhenClauseView) {
  const name =
    clause.kind === "param"
      ? `「${capabilityParamLabel(clause.key)}」`
      : `「${t(`image_workbench.model.fact_${clause.key}`)}」`;
  const right = Array.isArray(clause.right)
    ? clause.right.map((item) => renderValue(item)).join("、")
    : renderValue(clause.right);
  return `${name}${t(`image_workbench.model.op_${clause.operator}`)}「${right}」`;
}

async function loadModelOptions() {
  modelLoading.value = true;
  try {
    const response = await JobAPI.jobModelOptions();
    modelOptions.value = response.data.data ?? [];
  } catch {
    modelOptions.value = [];
  } finally {
    modelLoading.value = false;
  }
}

/** 切换模型：参考图清空（不同模型的上限与是否支持都不同），参数按新清单重新求值 */
function resetParamsForModel() {
  referenceImages.value = [];
  dirtyKeys.value = new Set();
  paramValues.value = {};
  syncParams();
}

watch(
  () => generateForm.value.model_pk,
  () => {
    resetParamsForModel();
    // 换模型即换掉不再适用的选区与画面来源：标记语法与坐标区间都可能不同
    resetCanvasState();
  }
);

function resetGenerateForm() {
  generateForm.value.prompt = "";
  resetParamsForModel();
  resetCanvasState();
  generateFormRef.value?.clearValidate();
}

// ─ 参考图上传 ───
function handleReferencePick(file: UploadFile) {
  const raw = file.raw;
  // 上传组件只做选择；选完即清空其内部列表，避免同名文件被去重拦住无法重选
  referenceUploadRef.value?.clearFiles();
  if (!raw) return;
  if (!raw.type.startsWith("image/")) {
    ElMessage.warning(t("image_workbench.job.referenceInvalidType"));
    return;
  }
  if (referenceImages.value.length >= maxReferenceImages.value) {
    ElMessage.warning(t("image_workbench.job.referenceLimit", { count: maxReferenceImages.value }));
    return;
  }
  void uploadReferenceImage(raw);
}

async function uploadReferenceImage(raw: File) {
  referenceUploading.value = true;
  try {
    const body = new FormData();
    body.append("file", raw);
    const response = await JobAPI.uploadReferenceImage(body);
    const result = response.data.data;
    if (!result?.file_path) {
      ElMessage.error(t("image_workbench.job.referenceUploadFailed"));
      return;
    }
    // has_alpha 在上传时即判出：依赖本次输入的参数（如透明通道）当场跟着变可用
    referenceImages.value = [
      ...referenceImages.value,
      { path: result.file_path, hasAlpha: !!result.has_alpha },
    ];
    syncParams();
  } catch {
    // 请求失败已由拦截器提示
  } finally {
    referenceUploading.value = false;
  }
}

function removeReferenceImage(index: number) {
  referenceImages.value = referenceImages.value.filter((_, idx) => idx !== index);
  syncParams();
}

// ── 画布与选区（design D3/D4/D8） ──

const promptInputRef = ref<{ textarea?: HTMLTextAreaElement } | null>(null);

const canvasVisible = ref(false);
/** 画布用的产物（图层拆分结果）；为空则画布显示参考图 */
const canvasArtifacts = ref<JobArtifact[]>([]);
const canvasJobId = ref<number | null>(null);
const canvasReferencePath = ref<string | null>(null);

/** 已指定的位置：序号与提示词里的区域占位符一一对应 */
const selections = ref<JobSelectionInput[]>([]);
/** 最后一次由画布导出并上传得到的参考图路径 */
const composedReferencePath = ref<string | null>(null);

/**
 * 画面来源：构成本次参考图的那次生成（design D3）。
 *
 * 两个条件缺一不可，且都从**当前实际状态**推出来，不靠人工记得去清：
 *
 * 1. 参考图恰好就是画布导出的那张合成图——用户随后手动增删参考图，它就不再是那次生成的
 *    拼合结果了，再提交这个标识等于给引用关系记一条假陈述；
 * 2. 画布展示的正是那次生成的产物（`canvasJobId`）——从表单打开的空白画布上画框再导出，
 *    画面来自别处，同样不能记成它的来源。
 */
const compositionSourceJobId = computed<number | null>(() =>
  composedReferencePath.value &&
  canvasJobId.value &&
  referenceImages.value.length === 1 &&
  referenceImages.value[0]?.path === composedReferencePath.value
    ? canvasJobId.value
    : null
);

/** 所选模型声明的坐标标记语法；没有它即「不支持交互编辑」，画布入口不出现（D8） */
const coordinateMarker = computed<CapabilityModeMarker | null>(() => {
  const marker = findCoordinateMarker(capability.value);
  return marker
    ? { placeholder: marker.placeholder, template: marker.template, range: marker.range }
    : null;
});

/** 占位符文本：写法取自声明（`区域{n}` → `区域1`），MUST NOT 硬编码 */
function placeholderText(index: number) {
  return (coordinateMarker.value?.placeholder ?? "区域{n}").replace("{n}", String(index));
}

/**
 * 把占位符插到提示词的**光标处**（design D4）。
 *
 * 标记在句中的位置本身就是语义（文档示例是「把那个**区域**换成花园」），所以位置由用户
 * 决定——服务端只能把标记追加在句首或句尾，那样做没有任何文档支持，等于赌。
 * 拿不到输入框时退化为追加：宁可插在末尾，也不能把用户刚划的框丢掉。
 */
function insertPlaceholder(index: number) {
  const text = placeholderText(index);
  const textarea = promptInputRef.value?.textarea;
  const current = generateForm.value.prompt ?? "";
  if (!textarea) {
    generateForm.value.prompt = current + text;
    return;
  }
  const start = textarea.selectionStart ?? current.length;
  const end = textarea.selectionEnd ?? start;
  generateForm.value.prompt = current.slice(0, start) + text + current.slice(end);
  const caret = start + text.length;
  nextTick(() => {
    textarea.focus();
    textarea.setSelectionRange(caret, caret);
  });
}

function openCanvasFromForm() {
  canvasArtifacts.value = [];
  canvasJobId.value = null;
  canvasReferencePath.value = referenceImages.value[0]?.path ?? null;
  canvasVisible.value = true;
}

/** 从历史详情「在此图上继续编辑」：把那次生成的产物当画面（design D1 的合成结果） */
async function continueEditing(row?: JobTable | null) {
  if (!row?.id) return;
  const modelOption = modelOptions.value.find((item) => item.id === row.model_pk);
  if (!modelOption || !findCoordinateMarker(modelOption.capability)) {
    ElMessage.warning(t("image_workbench.canvas.modelUnavailable"));
    return;
  }
  // 画布按**当前所选模型**的标记语法插入占位符，因此先把模型切过去；
  // 换模型的那次 watch 会清空画布状态，等它跑完再把这次的画面装回去
  generateForm.value.model_pk = row.model_pk;
  await nextTick();
  resetParamsForModel();
  canvasArtifacts.value = row.artifacts ?? [];
  canvasJobId.value = row.id;
  canvasReferencePath.value = null;
  detailVisible.value = false;
  canvasVisible.value = true;
}

function onCanvasPick(payload: { bbox: [number, number, number, number]; artifactId?: number }) {
  // 序号取当前最大值 +1：删掉中间某个选区后新加的不会顶掉别人的序号
  const index = selections.value.reduce((max, item) => Math.max(max, item.index), 0) + 1;
  selections.value = [
    ...selections.value,
    { index, bbox: payload.bbox, artifact_id: payload.artifactId },
  ];
  insertPlaceholder(index);
  ElMessage.success(t("image_workbench.canvas.picked", { index }));
}

/** 选区坐标 → 标签文案（`bbox` 在入参里可空，呈现为文本时统一处理） */
function selectionBboxText(item: JobSelectionInput) {
  return (item.bbox ?? []).join(", ");
}

function removeSelection(index: number) {
  selections.value = selections.value.filter((item) => item.index !== index);
}

async function onCanvasComposed(payload: { filePath: string; hasAlpha: boolean }) {
  // 合成图作为本次任务的参考图走的是既有的上传结果（design D1）
  referenceImages.value = [{ path: payload.filePath, hasAlpha: payload.hasAlpha }];
  // 「画面来源」以此为据：参考图就是这张合成图时，它才真的来自那次生成
  composedReferencePath.value = payload.filePath;
  canvasVisible.value = false;
  syncParams();
  ElMessage.success(t("image_workbench.canvas.composed"));
}

function handleCloseCanvas() {
  canvasVisible.value = false;
}

function resetCanvasState() {
  selections.value = [];
  composedReferencePath.value = null;
  canvasArtifacts.value = [];
  canvasJobId.value = null;
  canvasReferencePath.value = null;
}

async function handleGenerate() {
  const form = generateFormRef.value;
  if (!form) return;
  // 与 useCrudForm.handleSubmit 同一写法：校验失败时不发请求
  const valid = await (form.validate as () => Promise<boolean>)().catch(() => false);
  if (!valid) return;

  submitting.value = true;
  try {
    const response = await JobAPI.createJob({
      model_pk: generateForm.value.model_pk,
      prompt: generateForm.value.prompt,
      // 只提交用户改动过的参数；其余由服务端按清单补默认值（design D5）
      params: pickDirty(),
      reference_images: referenceImages.value.map((item) => item.path),
      // 选区与画面来源：服务端渲染占位符、校验坐标，并把来源产物记全（design D3/D4）
      selections: selections.value,
      composition_source_job_id: compositionSourceJobId.value ?? undefined,
    });
    ElMessage.success(t("image_workbench.job.submitted"));
    generateForm.value.prompt = "";
    resetParamsForModel();
    resetCanvasState();
    generateFormRef.value?.clearValidate();
    await refreshCreate();
    const created = response.data.data;
    if (created?.id) progressMessage.value = t("image_workbench.job.statusPending");
    syncStream();
  } catch {
    // 请求失败已由拦截器提示（并发闸门、参数越界等由后端再拦一层）
  } finally {
    submitting.value = false;
  }
}

// ─ 搜索 ───
type JobSearchForm = {
  prompt?: string;
  status?: JobStatus;
};

const searchForm = ref<JobSearchForm>({ prompt: undefined, status: undefined });
const showSearchBar = ref(true);
const searchBarRef = ref<InstanceType<typeof FaSearchBar> | null>(null);
const searchBarRules: Record<string, unknown> = {};

const statusOptions = computed(() => [
  { label: t("image_workbench.job.statusPending"), value: "pending" },
  { label: t("image_workbench.job.statusRunning"), value: "running" },
  { label: t("image_workbench.job.statusSuccess"), value: "success" },
  { label: t("image_workbench.job.statusFailed"), value: "failed" },
  { label: t("image_workbench.job.statusCancelled"), value: "cancelled" },
]);

const searchItems = computed<SearchFormItem[]>(() => [
  {
    label: t("image_workbench.job.prompt"),
    key: "prompt",
    type: "input",
    placeholder: t("image_workbench.job.promptSearchPlaceholder"),
    clearable: true,
    span: 12,
  },
  {
    label: t("image_workbench.common.status"),
    key: "status",
    type: "select",
    props: {
      placeholder: t("image_workbench.common.selectPlaceholder"),
      options: statusOptions.value,
      clearable: true,
    },
    span: 12,
  },
]);

// ─── 历史列表 ───
const ORDER_BY_DESC = JSON.stringify([{ id: "desc" }]);

function buildJobColumns(): ColumnOption<JobTable>[] {
  return [
    { type: "selection", width: 48, fixed: "left" },
    { type: "globalIndex", width: 56, label: t("image_workbench.common.index") },
    {
      prop: "prompt",
      label: t("image_workbench.job.prompt"),
      minWidth: 200,
      useSlot: true,
    },
    {
      prop: "model_display_name",
      label: t("image_workbench.job.model"),
      minWidth: 140,
      showOverflowTooltip: true,
      formatter: (row: JobTable) => row.model_display_name || row.model_id,
    },
    {
      prop: "params",
      label: t("image_workbench.job.params"),
      minWidth: 180,
      showOverflowTooltip: true,
      formatter: (row: JobTable) => summariseParams(row.params),
    },
    {
      prop: "status",
      label: t("image_workbench.common.status"),
      width: 110,
      useSlot: true,
    },
    {
      prop: "elapsed_ms",
      label: t("image_workbench.job.elapsed"),
      width: 100,
      formatter: (row: JobTable) => formatElapsed(row.elapsed_ms),
    },
    {
      prop: "artifacts",
      label: t("image_workbench.job.artifactCount"),
      width: 90,
      formatter: (row: JobTable) =>
        t("image_workbench.job.artifactCountValue", { count: row.artifacts?.length ?? 0 }),
    },
    {
      prop: "thumbnail",
      label: t("image_workbench.job.thumbnail"),
      width: 80,
      align: "center",
      useSlot: true,
    },
    {
      prop: "created_time",
      label: t("image_workbench.common.createdTime"),
      width: 168,
      showOverflowTooltip: true,
    },
    {
      prop: "operation",
      label: t("image_workbench.common.operation"),
      width: 220,
      fixed: "right",
      align: "center",
      formatter: (row: JobTable) => formatJobOperationCell(row),
    },
  ];
}

const faTableRef = ref<{ elTableRef?: { clearSelection: () => void } } | null>(null);
const { selectedIds, batchDeleting, onTableSelectionChange } = useTableSelection<JobTable>();

const {
  columns,
  columnChecks,
  data,
  loading,
  pagination,
  getData,
  replaceSearchParams,
  resetSearchParams,
  handleSizeChange,
  handleCurrentChange,
  refreshData,
  refreshCreate,
  refreshRemove,
  updateColumn,
} = useTable({
  core: {
    apiFn: JobAPI.listJob,
    apiParams: { page_no: 1, page_size: 10, order_by: ORDER_BY_DESC },
    columnsFactory: resolveStatusColumns<JobTable>(() => buildJobColumns()),
  },
});

// 语言切换后刷新列头文案（列配置工厂只在初始化时执行一次）
watch(locale, () => {
  updateColumn?.(buildJobColumns().map((col) => ({ prop: getColumnKey(col), updates: col })));
});

// 列表变化后重新对齐进度流：只在存在进行中的任务时保持连接
watch(data, () => syncStream());

async function handleSearchBarSearch(params: JobSearchForm) {
  await searchBarRef.value?.validate?.();
  replaceSearchParams({
    prompt: params.prompt,
    status: params.status,
    order_by: ORDER_BY_DESC,
  });
  await getData();
}

async function onResetSearch() {
  searchForm.value = { prompt: undefined, status: undefined };
  await resetSearchParams();
}

// ── SSE 进度订阅（Redis 兜底 + 断线重连） ───
const streamConnected = ref(false);
const progressMessage = ref("");
const streamRef = ref<JobStream | null>(null);

const trackedJob = computed(
  () => (data.value as JobTable[]).find((row) => isActiveStatus(row.status)) ?? null
);

/**
 * 连接只随「是否有进行中的任务」保持：
 * 后端建流时会先回放 Redis 中的进度快照，所以重连后不会停在「进行中」。
 */
function syncStream() {
  const stream = streamRef.value;
  if (!stream) return;
  const row = trackedJob.value;
  if (!row?.id) {
    stream.disconnect();
    return;
  }
  stream.connect(row.id);
}

function onStreamMessage(message: JobPushMessage) {
  const payload: JobProgress = message.data;
  const rows = data.value as JobTable[];
  const target = rows.find((row) => row.id === payload.job_id);
  if (target) {
    if (payload.status) target.status = payload.status;
    if (payload.error !== undefined) target.error = payload.error;
    if (payload.elapsed_ms !== undefined) target.elapsed_ms = payload.elapsed_ms;
  }
  progressMessage.value = payload.message ?? "";
  // 终态：整表刷新拿回服务端派生字段（status_label、产物与缩略图等）
  if (isTerminalStatus(payload.status)) {
    void refreshData().then(() => syncStream());
  }
}

function onStreamStatus(connected: boolean) {
  streamConnected.value = connected;
  // 断线重连后重新取一次当前状态，不停留在「进行中」
  if (connected) void refreshData();
}

onMounted(async () => {
  streamRef.value = new JobStream({ onMessage: onStreamMessage, onStatus: onStreamStatus });
  await loadModelOptions();
});

// 缓存页面切走时必须断流，切回按需重连（onUnmounted 不是唯一清理点）
onActivated(() => syncStream());
onDeactivated(() => {
  streamRef.value?.disconnect();
  streamConnected.value = false;
});
onUnmounted(() => {
  streamRef.value?.disconnect();
  streamRef.value = null;
});

// ─── 详情 ───
const detailVisible = ref(false);
const detailJob = ref<JobTable | null>(null);

const detailItems = computed<DescriptionsItem[]>(() => [
  { label: t("image_workbench.job.prompt"), prop: "prompt", slot: "prompt", span: 2 },
  {
    label: t("image_workbench.job.selections"),
    prop: "selections",
    slot: "selections",
    span: 2,
  },
  { label: t("image_workbench.job.model"), prop: "model_display_name" },
  { label: t("image_workbench.job.modelId"), prop: "model_id" },
  { label: t("image_workbench.job.service"), prop: "service_name" },
  { label: t("image_workbench.common.status"), prop: "status", slot: "status" },
  { label: t("image_workbench.job.elapsed"), prop: "elapsed_ms" },
  { label: t("image_workbench.job.params"), prop: "params", slot: "params", span: 2 },
  {
    label: t("image_workbench.job.referenceImages"),
    prop: "reference_images",
    slot: "reference_images",
    span: 2,
  },
  { label: t("image_workbench.job.usage"), prop: "usage", slot: "usage", span: 2 },
  { label: t("image_workbench.job.error"), prop: "error", span: 2 },
  { label: t("image_workbench.job.artifacts"), prop: "artifacts", slot: "artifacts", span: 2 },
  { label: t("image_workbench.job.startedAt"), prop: "started_at" },
  { label: t("image_workbench.job.finishedAt"), prop: "finished_at" },
  { label: t("image_workbench.common.createdTime"), prop: "created_time" },
]);

async function openDetail(row: JobTable) {
  if (row.id == null) return;
  try {
    const response = await JobAPI.detailJob(row.id);
    detailJob.value = response.data.data ?? null;
  } catch {
    detailJob.value = row;
  }
  detailVisible.value = true;
}

function handleCloseDetail() {
  detailVisible.value = false;
  detailJob.value = null;
}

// ─── 行操作：取消 / 重试 / 下载 / 删除 ───
/** 仅当任务只有一张产物时才给出行内下载：多产物时「下载哪个」无从表达，进详情逐张下 */
function singleArtifactId(row: JobTable) {
  const artifacts = row.artifacts ?? [];
  return artifacts.length === 1 ? (artifacts[0]?.id ?? null) : null;
}

/** 是否需要在详情里呈现「最终提示词」：两个形态相同时只呈现一份 */
function hasDistinctFinalPrompt(row?: JobTable | null) {
  return !!row?.prompt_final && row.prompt_final !== row.prompt;
}

/** 详情里的接续入口：这次生成有产物、且它的模型仍可选并声明了交互编辑 */
function canContinueEditing(row?: JobTable | null) {
  if (!row?.id || (row.artifacts?.length ?? 0) === 0) return false;
  const modelOption = modelOptions.value.find((item) => item.id === row.model_pk);
  return !!modelOption && !!findCoordinateMarker(modelOption.capability);
}

function buildJobRowActions(row: JobTable): TableOperationAction[] {
  const actions: TableOperationAction[] = [
    {
      key: "detail",
      label: t("image_workbench.common.detail"),
      artType: "view",
      perm: "image_workbench:job:query",
      run: () => {
        void openDetail(row);
      },
    },
  ];

  if (isActiveStatus(row.status)) {
    actions.push({
      key: "cancel",
      label: t("image_workbench.job.cancel"),
      artType: "more",
      icon: "ri:stop-circle-line",
      perm: "image_workbench:job:create",
      run: () => {
        void cancelJob(row);
      },
    });
  }

  if (isTerminalStatus(row.status)) {
    actions.push({
      key: "retry",
      label: t("image_workbench.job.retry"),
      artType: "more",
      icon: "ri:refresh-line",
      perm: "image_workbench:job:create",
      run: () => {
        void retryJob(row);
      },
    });
  }

  const artifactId = row.status === "success" ? singleArtifactId(row) : null;
  if (artifactId != null) {
    actions.push({
      key: "download",
      label: t("image_workbench.job.download"),
      artType: "view",
      icon: "ri:download-line",
      perm: "image_workbench:job:download",
      run: () => {
        void downloadArtifact(row, artifactId);
      },
    });
  }

  if (isTerminalStatus(row.status)) {
    actions.push({
      key: "delete",
      label: t("image_workbench.common.delete"),
      artType: "delete",
      perm: "image_workbench:job:delete",
      run: () => {
        void deleteJob(row);
      },
    });
  }

  return actions;
}

function formatJobOperationCell(row: JobTable) {
  return renderTableOperationCell(buildJobRowActions(row), {
    wrapperClass: "inline-flex flex-wrap items-center justify-end gap-1",
  });
}

/** 取消：明确告知不保证免除上游费用（我们并不掌握上游计费时点） */
async function cancelJob(row: JobTable) {
  try {
    await confirmAction(
      t("image_workbench.job.cancelConfirm"),
      t("image_workbench.job.cancelTitle")
    );
    await JobAPI.cancelJob(row.id!);
    ElMessage.success(t("image_workbench.job.cancelledToast"));
    await refreshData();
  } catch {
    // 用户取消或已被拒绝（已由拦截器提示）
  }
}

async function retryJob(row: JobTable) {
  try {
    await confirmAction(t("image_workbench.job.retryConfirm"), t("image_workbench.job.retryTitle"));
    await JobAPI.retryJob(row.id!);
    ElMessage.success(t("image_workbench.job.retried"));
    await refreshCreate();
    syncStream();
  } catch {
    // 用户取消或已被拒绝（如并发闸门）
  }
}

/** 下载单张产物（行内入口只在任务恰好一张产物时出现） */
async function downloadArtifact(row: JobTable, artifactId: number) {
  try {
    const response = await JobAPI.downloadArtifact(row.id!, artifactId);
    const blob = response.data as Blob;
    saveAs(
      blob,
      filenameFromDisposition(response.headers?.["content-disposition"], `${row.id}-artifact.png`)
    );
    ElMessage.success(t("image_workbench.job.downloadStarted"));
  } catch {
    ElMessage.error(t("image_workbench.job.downloadFailed"));
  }
}

function filenameFromDisposition(header: string | undefined, fallback: string): string {
  if (!header) return fallback;
  const utf8 = /filename\*=UTF-8''([^;]+)/i.exec(header);
  if (utf8?.[1]) return decodeURIComponent(utf8[1]);
  const plain = /filename="?([^";]+)"?/i.exec(header);
  return plain?.[1] ?? fallback;
}

/**
 * 删除被拒时的呈现（spec: 删除绘图任务「拒绝删除，并指出引用了它的任务」）。
 *
 * 引用保护要把**哪些任务引用了它**完整列出来——那是一条用户要照着去操作的信息，一闪而过的
 * 提示条装不下（提示条会很快消失，且长文案会被截断）。因此删除接口关掉自动提示，由这里
 * 按答复内容决定是弹一条要读的信息，还是照常报错。
 */
async function presentDeleteRejection(error: unknown) {
  const message = error instanceof Error ? error.message : "";
  if (!message) return;
  // 后端在有引用者时会把它们的标识写进答复（形如「…#12、#15…」）
  if (/#\d+/.test(message)) {
    await ElMessageBox.alert(message, t("image_workbench.job.deleteBlockedTitle"), {
      confirmButtonText: t("image_workbench.common.confirm"),
      type: "warning",
    });
    return;
  }
  ElMessage.error(message);
}

/** 删除：软删记录 + 真删产物，界面明确告知产物不可恢复 */
async function confirmThenDelete(ids: number[], message: string, title: string) {
  try {
    await confirmAction(message, title);
  } catch {
    return; // 用户自己取消了确认框，不是错误
  }
  try {
    await JobAPI.deleteJob(ids, false);
    faTableRef.value?.elTableRef?.clearSelection();
    await refreshRemove();
  } catch (error) {
    await presentDeleteRejection(error);
  }
}

async function deleteJob(row: JobTable) {
  await confirmThenDelete(
    [row.id!],
    t("image_workbench.job.deleteConfirm"),
    t("image_workbench.job.deleteTitle")
  );
}

async function handleBatchDelete() {
  const ids = selectedIds.value;
  if (ids.length === 0) return;
  batchDeleting.value = true;
  try {
    await confirmThenDelete(
      ids,
      t("image_workbench.job.batchDeleteConfirm", { count: ids.length }),
      t("image_workbench.job.deleteTitle")
    );
  } finally {
    batchDeleting.value = false;
  }
}
</script>
