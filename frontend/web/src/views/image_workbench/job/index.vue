<!-- 绘图工作台 - 工作台：左侧生成表单（按所选模型的能力声明联动）+ 右侧历史列表与详情 -->
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
                <template #size>
                  <ElSelect
                    v-model="generateForm.size"
                    :placeholder="t('image_workbench.job.sizePlaceholder')"
                    clearable
                    filterable
                    :allow-create="allowCustomSize"
                    default-first-option
                    class="w-full"
                  >
                    <ElOption
                      v-for="option in sizeOptions"
                      :key="option.value"
                      :label="option.label"
                      :value="option.value"
                    />
                  </ElSelect>
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
                          :key="item"
                          class="relative h-16 w-16 overflow-hidden rounded border border-(--el-border-color)"
                        >
                          <img :src="staticUrl(item)" class="h-full w-full object-cover" alt="" />
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
                  v-if="artifactUrl(row)"
                  :src="artifactUrl(row)"
                  fit="cover"
                  class="h-10 w-10 rounded"
                  :preview-src-list="[artifactUrl(row)]"
                  preview-teleported
                />
                <span v-else class="text-xs text-(--el-text-color-secondary)">—</span>
              </template>
            </FaTable>
          </ElCard>
        </div>
      </ElSplitterPanel>
    </ElSplitter>

    <!-- 任务详情 -->
    <FaDialog
      v-model="detailVisible"
      :title="t('image_workbench.job.detailTitle')"
      width="720px"
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
        <template #status="{ row }">
          <FaStatusTag
            :type="jobStatusTagType(asJob(row)?.status)"
            :label="jobStatusLabel(asJob(row)?.status)"
          />
        </template>
        <template #thumbnail="{ row }">
          <ElImage
            v-if="artifactUrl(asJob(row))"
            :src="artifactUrl(asJob(row))"
            fit="contain"
            class="max-h-40"
            :preview-src-list="[artifactUrl(asJob(row))]"
            preview-teleported
          />
          <span v-else class="text-xs text-(--el-text-color-secondary)">
            {{ t("image_workbench.job.noArtifact") }}
          </span>
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
      </FaDescriptions>
    </FaDialog>
  </div>
</template>

<script setup lang="ts">
import JobAPI, {
  JobStream,
  type JobModelOption,
  type JobProgress,
  type JobPushMessage,
  type JobStatus,
  type JobTable,
} from "@/api/module_image_workbench/job";
import { renderTableOperationCell, resolveStatusColumns, type TableOperationAction } from "@utils";
import { getColumnKey } from "@/hooks/core/useTableColumns";
import { Plus, Close, Delete } from "@element-plus/icons-vue";
import { saveAs } from "file-saver";
import type { ColumnOption } from "@/types/component";
import type { SearchFormItem } from "@/components/forms/fa-search-bar/index.vue";
import type { FormItem } from "@/components/forms/fa-form/index.vue";
import type { DescriptionsItem } from "@/components/display/fa-descriptions/index.vue";
import type { UploadFile, UploadInstance } from "element-plus";
import { ElMessage } from "element-plus";
import FaSearchBar from "@/components/forms/fa-search-bar/index.vue";
import FaForm from "@/components/forms/fa-form/index.vue";
import FaDescriptions from "@/components/display/fa-descriptions/index.vue";
import FaTableHeader from "@/components/tables/fa-table-header/index.vue";
import FaTableHeaderLeft from "@/components/tables/fa-table-header-left/index.vue";

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

/**
 * 参考图预览地址。上传接口回的 file_path 形如 `static/upload/file/2026/09/16/x.png`，
 * 是相对后端工作目录的路径；静态资源的公开地址是 `{ROOT_PATH}{STATIC_URL}` = `/api/v1/static`
 * 再加上相对 static 的那一段（即去掉开头的 `static/`）。
 */
function staticUrl(filePath: string) {
  const relative = filePath.replace(/^\/+/, "").replace(/^static\//, "");
  return `${import.meta.env.VITE_APP_BASE_API}/static/${relative}`;
}

/**
 * 产物缩略地址：后端在本地存储节点下给出 image_url（/static/upload/...）；
 * 非本地协议时为空，此时只能走下载接口取回。
 */
function artifactUrl(row?: JobTable | null) {
  if (!row?.image_path) return "";
  return row.image_url || "";
}

function asJob(row: unknown): JobTable | null {
  return (row as JobTable | null) ?? null;
}

function referenceListOf(row?: JobTable | null) {
  return row?.params?.reference_images ?? [];
}

function usageText(row?: JobTable | null) {
  const usage = row?.usage;
  if (!usage) return "—";
  const images = usage.images ?? 0;
  const upstream = usage.upstream ? JSON.stringify(usage.upstream) : "—";
  return `${t("image_workbench.job.usageImages")}: ${images}；${t("image_workbench.job.usageUpstream")}: ${upstream}`;
}

// ── 生成表单 ───
type GenerateForm = {
  model_pk?: number;
  prompt: string;
  size?: string;
  output_format?: string;
};

const generateFormRef = ref<InstanceType<typeof FaForm> | null>(null);
const generateForm = ref<GenerateForm>({
  model_pk: undefined,
  prompt: "",
  size: undefined,
  output_format: undefined,
});

const modelOptions = ref<JobModelOption[]>([]);
const modelLoading = ref(false);
const submitting = ref(false);
const referenceImages = ref<string[]>([]);
const referenceUploading = ref(false);
const referenceUploadRef = ref<UploadInstance | null>(null);

const selectedModel = computed(
  () => modelOptions.value.find((item) => item.id === generateForm.value.model_pk) ?? null
);
const capability = computed(() => selectedModel.value?.capability ?? null);
const sizeOptions = computed(() =>
  (capability.value?.sizes ?? []).map((item) => ({ label: item, value: item }))
);
const formatOptions = computed(() =>
  (capability.value?.output_formats ?? []).map((item) => ({ label: item, value: item }))
);
const allowReference = computed(() => !!capability.value?.support_reference);
const maxReferenceImages = computed(() => capability.value?.max_reference_images ?? 0);
const allowCustomSize = computed(() => !!capability.value?.allow_custom_size);

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
  {
    label: t("image_workbench.job.prompt"),
    key: "prompt",
    type: "input",
    span: 24,
    props: {
      type: "textarea",
      rows: 4,
      maxlength: 4000,
      showWordLimit: true,
      placeholder: t("image_workbench.job.promptPlaceholder"),
    },
  },
  { label: t("image_workbench.job.size"), key: "size", span: 24 },
  {
    label: t("image_workbench.job.outputFormat"),
    key: "output_format",
    type: "select",
    span: 24,
    props: {
      placeholder: t("image_workbench.common.selectPlaceholder"),
      options: formatOptions.value,
      clearable: true,
    },
  },
  { label: t("image_workbench.job.referenceImages"), key: "reference_images", span: 24 },
]);

type RuleCallback = (error?: Error) => void;

/** 尺寸取值必须落在能力声明内；允许自定义时须为「宽x高」 */
function sizeValidator(_rule: unknown, value: unknown, callback: RuleCallback) {
  const text = String(value ?? "").trim();
  if (!text) {
    callback();
    return;
  }
  const sizes = capability.value?.sizes ?? [];
  if (sizes.includes(text)) {
    callback();
    return;
  }
  if (allowCustomSize.value && /^\d+[xX*]\d+$/.test(text)) {
    callback();
    return;
  }
  callback(
    new Error(
      sizes.length > 0
        ? t("image_workbench.job.sizeInvalid", { sizes: sizes.join("、") })
        : t("image_workbench.job.sizeNotDeclared")
    )
  );
}

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
  size: [{ validator: sizeValidator, trigger: "change" }],
  reference_images: [{ validator: referenceValidator, trigger: "change" }],
}));

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

/**
 * 切换模型时按该模型的能力声明重置参数：
 * 尺寸取该模型声明的第一档、输出格式取声明的默认值；不支持参考图则清空已选参考图。
 */
function applyCapabilityDefaults() {
  const cap = capability.value;
  referenceImages.value = [];
  if (!cap) {
    generateForm.value.size = undefined;
    generateForm.value.output_format = undefined;
    return;
  }
  generateForm.value.size = cap.sizes?.[0];
  generateForm.value.output_format = cap.default_output_format || cap.output_formats?.[0];
}

watch(
  () => generateForm.value.model_pk,
  () => applyCapabilityDefaults()
);

function resetGenerateForm() {
  generateForm.value.prompt = "";
  applyCapabilityDefaults();
  generateFormRef.value?.clearValidate();
}

// ── 参考图上传 ───
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
    const path = response.data.data?.file_path;
    if (!path) {
      ElMessage.error(t("image_workbench.job.referenceUploadFailed"));
      return;
    }
    referenceImages.value = [...referenceImages.value, path];
  } catch {
    // 请求失败已由拦截器提示
  } finally {
    referenceUploading.value = false;
  }
}

function removeReferenceImage(index: number) {
  referenceImages.value = referenceImages.value.filter((_, idx) => idx !== index);
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
      size: generateForm.value.size || undefined,
      output_format: generateForm.value.output_format || undefined,
      reference_images: referenceImages.value,
    });
    ElMessage.success(t("image_workbench.job.submitted"));
    generateForm.value.prompt = "";
    referenceImages.value = [];
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

// ── 搜索 ───
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
      minWidth: 150,
      formatter: (row: JobTable) =>
        [row.params?.size, row.params?.output_format].filter(Boolean).join(" · ") || "—",
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
    if (payload.image_path !== undefined) target.image_path = payload.image_path;
  }
  progressMessage.value = payload.message ?? "";
  // 终态：整表刷新拿回服务端派生字段（status_label、image_url 等）
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
  { label: t("image_workbench.job.prompt"), prop: "prompt", span: 2 },
  { label: t("image_workbench.job.model"), prop: "model_display_name" },
  { label: t("image_workbench.job.modelId"), prop: "model_id" },
  { label: t("image_workbench.job.service"), prop: "service_name" },
  { label: t("image_workbench.common.status"), prop: "status", slot: "status" },
  { label: t("image_workbench.job.size"), prop: "params.size" },
  { label: t("image_workbench.job.outputFormat"), prop: "params.output_format" },
  {
    label: t("image_workbench.job.referenceImages"),
    prop: "reference_images",
    slot: "reference_images",
    span: 2,
  },
  { label: t("image_workbench.job.elapsed"), prop: "elapsed_ms" },
  { label: t("image_workbench.job.imageSize"), prop: "image_size" },
  { label: t("image_workbench.job.usage"), prop: "usage", slot: "usage", span: 2 },
  { label: t("image_workbench.job.error"), prop: "error", span: 2 },
  { label: t("image_workbench.job.thumbnail"), prop: "thumbnail", slot: "thumbnail", span: 2 },
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

  if (row.status === "success" && row.image_path) {
    actions.push({
      key: "download",
      label: t("image_workbench.job.download"),
      artType: "view",
      icon: "ri:download-line",
      perm: "image_workbench:job:download",
      run: () => {
        void downloadJob(row);
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

/** 下载产物：后端返回二进制附件，文件名形如 {uuid}.{ext} */
async function downloadJob(row: JobTable) {
  try {
    const response = await JobAPI.downloadJob(row.id!);
    const blob = response.data as Blob;
    saveAs(
      blob,
      filenameFromDisposition(
        response.headers?.["content-disposition"],
        `${row.uuid ?? row.id}.png`
      )
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

/** 删除：软删记录 + 真删产物，界面明确告知产物不可恢复 */
async function deleteJob(row: JobTable) {
  try {
    await confirmDelete(t("image_workbench.job.deleteConfirm"));
    await JobAPI.deleteJob([row.id!]);
    faTableRef.value?.elTableRef?.clearSelection();
    await refreshRemove();
  } catch {
    // 用户取消或进行中任务被拒绝（已由拦截器提示需先取消）
  }
}

async function handleBatchDelete() {
  const ids = selectedIds.value;
  if (ids.length === 0) return;
  try {
    await confirmAction(
      t("image_workbench.job.batchDeleteConfirm", { count: ids.length }),
      t("image_workbench.job.deleteTitle")
    );
    batchDeleting.value = true;
    await JobAPI.deleteJob(ids);
    faTableRef.value?.elTableRef?.clearSelection();
    await refreshRemove();
  } catch {
    // 用户取消或进行中任务被拒绝（已由拦截器提示需先取消）
  } finally {
    batchDeleting.value = false;
  }
}
</script>
