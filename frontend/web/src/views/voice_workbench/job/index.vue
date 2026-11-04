<template>
  <div class="fa-full-height">
    <ElSplitter :style="'height: 100%'">
      <ElSplitterPanel size="42%" :min="480">
        <div class="flex h-full min-h-0 flex-col gap-4 overflow-y-auto pr-2">
          <ElCard class="fa-table-card voice-job-card" shadow="never">
            <template #header>
              <div class="flex flex-wrap items-center justify-between gap-2">
                <span class="font-semibold">{{ t("voice_workbench.job.title") }}</span>
                <ElTag v-if="activeCount > 0" type="warning">
                  {{ t("voice_workbench.job.runningHint") }}
                </ElTag>
              </div>
            </template>

            <ElAlert
              v-if="!modelLoading && modelOptions.length === 0"
              type="warning"
              :closable="false"
              show-icon
            >
              <div class="flex flex-wrap items-center gap-2">
                <span>{{ t("voice_workbench.job.noModelHint") }}</span>
                <ElButton
                  v-hasPerm="['voice_workbench:service:query']"
                  link
                  type="primary"
                  @click="goConfig"
                >
                  {{ t("voice_workbench.job.goConfig") }}
                </ElButton>
              </div>
            </ElAlert>

            <template v-else>
              <FaForm
                ref="modelFormRef"
                v-model="jobForm"
                :items="modelFormItems"
                :rules="modelRules"
                label-suffix=":"
                :label-width="96"
                label-position="right"
                :span="24"
                :show-reset="false"
                :show-submit="false"
              />

              <div class="mt-2">
                <div
                  v-hasPerm="['voice_workbench:job:add']"
                  class="flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-(--el-border-color) p-6 text-center transition-colors hover:border-(--el-color-primary)"
                  @click="openFilePicker"
                  @dragover.prevent
                  @drop.prevent="handleDrop"
                >
                  <ElIcon class="text-2xl"><Upload /></ElIcon>
                  <div class="text-sm font-medium">{{ t("voice_workbench.job.dropHint") }}</div>
                  <div class="text-xs text-(--el-text-color-secondary)">
                    {{ t("voice_workbench.job.browseHint") }}
                  </div>
                </div>
                <input
                  ref="fileInputRef"
                  class="hidden"
                  type="file"
                  accept="audio/*"
                  multiple
                  @change="handleFilePick"
                />
              </div>

              <div class="mt-3 flex flex-wrap items-center gap-2">
                <ElButton
                  v-hasPerm="['voice_workbench:job:add']"
                  type="primary"
                  :icon="Upload"
                  :disabled="!canStart"
                  :loading="running"
                  @click="startBatch"
                >
                  {{ t("voice_workbench.job.start") }}
                </ElButton>
                <ElButton :disabled="running || uploadRows.length === 0" @click="clearRows">
                  {{ t("voice_workbench.job.clear") }}
                </ElButton>
                <span class="text-xs text-(--el-text-color-secondary)">
                  {{ t("voice_workbench.job.fileLimit", { count: MAX_FILES }) }}
                </span>
              </div>
            </template>
          </ElCard>

          <ElCard class="fa-table-card voice-job-card" shadow="never">
            <template #header>
              <div class="flex flex-wrap items-center justify-between gap-2">
                <span class="font-semibold">{{ t("voice_workbench.job.queueTitle") }}</span>
                <span class="text-xs text-(--el-text-color-secondary)">
                  {{
                    t("voice_workbench.job.queueCount", {
                      count: uploadRows.length,
                      limit: MAX_FILES,
                    })
                  }}
                </span>
              </div>
            </template>

            <ElEmpty v-if="uploadRows.length === 0" :description="t('voice_workbench.job.queueEmpty')" />

            <ElTable
              v-else
              :data="uploadRows"
              row-key="key"
              highlight-current-row
              :current-row-key="selectedRowKey ?? undefined"
              @row-click="selectRow"
            >
              <ElTableColumn
                :label="t('voice_workbench.history.originalFilename')"
                min-width="180"
              >
                <template #default="{ row }">
                  <div class="min-w-0">
                    <div class="break-all">{{ row.file.name }}</div>
                    <div v-if="row.error" class="mt-1 text-xs text-(--el-color-danger)">
                      {{ row.error }}
                    </div>
                  </div>
                </template>
              </ElTableColumn>
              <ElTableColumn :label="t('voice_workbench.history.fileSize')" width="100">
                <template #default="{ row }">{{ formatBytes(row.file.size) }}</template>
              </ElTableColumn>
              <ElTableColumn :label="t('voice_workbench.common.status')" width="120">
                <template #default="{ row }">
                  <FaStatusTag
                    :type="rowStatusTagType(row.status)"
                    :label="rowStatusLabel(row.status)"
                  />
                </template>
              </ElTableColumn>
              <ElTableColumn
                :label="t('voice_workbench.common.operation')"
                width="190"
                fixed="right"
                align="center"
              >
                <template #default="{ row }">
                  <div class="inline-flex flex-wrap items-center justify-end gap-1">
                    <ElButton
                      v-if="row.status === 'failed'"
                      link
                      type="primary"
                      :disabled="running"
                      @click.stop="retryRow(row)"
                    >
                      {{ t("voice_workbench.job.retry") }}
                    </ElButton>
                    <ElButton v-if="row.job" link type="primary" @click.stop="selectRow(row)">
                      {{ t("voice_workbench.job.viewResult") }}
                    </ElButton>
                    <ElButton
                      link
                      type="danger"
                      :disabled="running || isActiveRow(row)"
                      @click.stop="removeRow(row)"
                    >
                      {{ t("voice_workbench.common.delete") }}
                    </ElButton>
                  </div>
                </template>
              </ElTableColumn>
            </ElTable>
          </ElCard>

          <ElCard class="fa-table-card voice-job-card" shadow="never">
            <template #header>
              <span class="font-semibold">{{ t("voice_workbench.job.resultTitle") }}</span>
            </template>
            <ResultPanel :job="selectedJob" :filename="selectedFilename" />
          </ElCard>
        </div>
      </ElSplitterPanel>

      <ElSplitterPanel :min="420">
        <div class="flex h-full min-h-0 flex-col overflow-y-auto pl-2">
          <HistoryPanel ref="historyRef" />
        </div>
      </ElSplitterPanel>
    </ElSplitter>
  </div>
</template>

<script setup lang="ts">
import JobAPI, { type JobModelOption, type JobTable } from "@/api/module_voice_workbench/job";
import { Upload } from "@element-plus/icons-vue";
import { ElMessage } from "element-plus";
import type { FormItem } from "@/components/forms/fa-form/index.vue";
import FaForm from "@/components/forms/fa-form/index.vue";
import {
  AudioDecodeError,
  TARGET_SAMPLE_RATE,
  transcodeAudioFile,
} from "./audio";
import { runSequential } from "./batch";
import ResultPanel from "./components/ResultPanel.vue";
import HistoryPanel from "./components/HistoryPanel.vue";

// 与菜单 route_name 一致：KeepAlive 的 include / exclude 按组件 name 匹配
defineOptions({
  name: "VoiceWorkbenchJob",
  inheritAttrs: false,
});

const { t } = useI18n();
const router = useRouter();

const MAX_FILES = 10;
const AUDIO_EXTENSIONS = /\.(mp3|wav|m4a|aac|ogg|oga|opus|flac|webm|wma|amr|3gp)$/i;

type RowStatus = "pending" | "transcoding" | "recognizing" | "success" | "failed";

interface UploadRow {
  key: string;
  file: File;
  status: RowStatus;
  error?: string;
  job?: JobTable;
  /** 转码结果缓存：失败行重试时不必重新解码 */
  wav?: ArrayBuffer;
}

const ROW_STATUS_I18N: Record<RowStatus, string> = {
  pending: "statusPending",
  transcoding: "statusTranscoding",
  recognizing: "statusRecognizing",
  success: "statusSuccess",
  failed: "statusFailed",
};

const modelOptions = ref<JobModelOption[]>([]);
const modelLoading = ref(true);
const jobForm = ref<{ model_pk?: number }>({ model_pk: undefined });
const modelFormRef = ref<InstanceType<typeof FaForm> | null>(null);

const modelSelectOptions = computed(() =>
  modelOptions.value.map((item) => ({ label: item.display_name, value: item.id }))
);

const selectedModel = computed(
  () => modelOptions.value.find((item) => item.id === jobForm.value.model_pk) ?? null
);

const modelFormItems = computed<FormItem[]>(() => [
  {
    label: t("voice_workbench.job.model"),
    key: "model_pk",
    type: "select",
    span: 24,
    props: {
      placeholder: t("voice_workbench.job.modelPlaceholder"),
      options: modelSelectOptions.value,
      filterable: true,
      loading: modelLoading.value,
      disabled: running.value,
    },
  },
]);

const modelRules = computed(() => ({
  model_pk: [
    { required: true, message: t("voice_workbench.job.modelRequired"), trigger: "change" },
  ],
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

watch(
  modelOptions,
  (options) => {
    // 只有一个可用模型时自动选中，少一步操作
    if (options.length === 1) {
      jobForm.value.model_pk = options[0]!.id;
      return;
    }
    if (jobForm.value.model_pk && !options.some((item) => item.id === jobForm.value.model_pk)) {
      jobForm.value.model_pk = undefined;
    }
  },
  { immediate: true }
);

function goConfig() {
  void router.push({ name: "VoiceWorkbenchConfig" });
}

const fileInputRef = ref<HTMLInputElement | null>(null);
const uploadRows = ref<UploadRow[]>([]);
const selectedRowKey = ref<string | null>(null);
const running = ref(false);
let rowSeq = 0;

const activeCount = computed(
  () => uploadRows.value.filter((row) => isActiveRow(row)).length
);
const pendingCount = computed(
  () =>
    uploadRows.value.filter((row) => row.status === "pending" || row.status === "failed").length
);
const canStart = computed(
  () => !running.value && !!selectedModel.value && pendingCount.value > 0
);
const selectedRow = computed(
  () => uploadRows.value.find((row) => row.key === selectedRowKey.value) ?? null
);
const selectedJob = computed(() => selectedRow.value?.job ?? null);
const selectedFilename = computed(() => selectedRow.value?.file.name ?? null);

function isAudioFile(file: File) {
  return file.type.startsWith("audio/") || AUDIO_EXTENSIONS.test(file.name);
}

function openFilePicker() {
  if (running.value) return;
  fileInputRef.value?.click();
}

function handleFilePick(event: Event) {
  const input = event.target as HTMLInputElement;
  addFiles(Array.from(input.files ?? []));
  // 允许重复选择同一个文件
  input.value = "";
}

function handleDrop(event: DragEvent) {
  if (running.value) return;
  addFiles(Array.from(event.dataTransfer?.files ?? []));
}

function addFiles(files: File[]) {
  if (files.length === 0) return;

  const audioFiles = files.filter(isAudioFile);
  if (audioFiles.length !== files.length) {
    ElMessage.warning(t("voice_workbench.job.invalidAudio"));
  }

  const remaining = MAX_FILES - uploadRows.value.length;
  if (remaining <= 0) {
    ElMessage.warning(t("voice_workbench.job.fileLimit", { count: MAX_FILES }));
    return;
  }

  const accepted = audioFiles.slice(0, remaining);
  if (audioFiles.length > remaining) {
    ElMessage.warning(
      t("voice_workbench.job.overLimit", {
        count: MAX_FILES,
        ignored: audioFiles.length - remaining,
      })
    );
  }

  const rows = accepted.map<UploadRow>((file) => ({
    key: `row-${rowSeq++}`,
    file,
    status: "pending",
  }));
  uploadRows.value = [...uploadRows.value, ...rows];
  if (!selectedRowKey.value && rows.length > 0) {
    selectedRowKey.value = rows[0]!.key;
  }
}

// ElTable 的插槽行由组件以宽松 Record 下发，进入业务函数前收回具体类型
function asUploadRow(row: Record<PropertyKey, any>): UploadRow {
  return row as UploadRow;
}

function selectRow(row: Record<PropertyKey, any>) {
  selectedRowKey.value = asUploadRow(row).key;
}

function isActiveRow(row: Record<PropertyKey, any>) {
  return row.status === "transcoding" || row.status === "recognizing";
}

function removeRow(row: Record<PropertyKey, any>) {
  const target = asUploadRow(row);
  if (running.value || isActiveRow(row)) return;
  uploadRows.value = uploadRows.value.filter((item) => item.key !== target.key);
  if (selectedRowKey.value === target.key) {
    selectedRowKey.value = uploadRows.value[0]?.key ?? null;
  }
}

function clearRows() {
  if (running.value) return;
  uploadRows.value = [];
  selectedRowKey.value = null;
}

function rowStatusLabel(status: RowStatus) {
  return t(`voice_workbench.job.${ROW_STATUS_I18N[status]}`);
}

function rowStatusTagType(status: RowStatus) {
  if (status === "success") return "success";
  if (status === "failed") return "danger";
  if (status === "pending") return "primary";
  return "warning";
}

function formatBytes(value?: number | null) {
  if (value == null) return "—";
  if (value < 1024) return `${value} B`;
  const kb = value / 1024;
  if (kb < 1024) return `${kb.toFixed(1)} KB`;
  return `${(kb / 1024).toFixed(1)} MB`;
}

function formatClock(seconds: number) {
  const total = Math.max(0, Math.round(seconds));
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  return `${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}`;
}

/** 请求失败时拦截器可能以 HttpError 或原始 AxiosResponse 拒绝，两种都取到可读文案 */
function describeError(error: unknown) {
  if (error instanceof AudioDecodeError) {
    return t("voice_workbench.job.decodeFailed");
  }
  if (error instanceof Error && error.message) {
    return error.message;
  }
  const response = error as { data?: { msg?: string } } | null;
  if (response?.data?.msg) return response.data.msg;
  return t("voice_workbench.job.unexpectedError");
}

function failRow(row: UploadRow, error: unknown) {
  row.status = "failed";
  row.error = describeError(error);
}

async function transcribeRow(row: UploadRow, model: JobModelOption): Promise<JobTable> {
  try {
    row.error = undefined;
    row.job = undefined;

    let wav = row.wav;
    if (!wav) {
      row.status = "transcoding";
      wav = await transcodeAudioFile(row.file);
      row.wav = wav;
    }

    // 限额校验以实际上传的转码 WAV 为准，与后端同一口径
    if (wav.byteLength > model.max_file_mb * 1024 * 1024) {
      throw new Error(
        t("voice_workbench.job.fileTooLarge", {
          size: formatBytes(wav.byteLength),
          limit: model.max_file_mb,
        })
      );
    }
    const durationSec = Math.ceil(
      Math.max(0, wav.byteLength - 44) / (TARGET_SAMPLE_RATE * 2)
    );
    if (durationSec > model.max_duration_sec) {
      throw new Error(
        t("voice_workbench.job.durationTooLong", {
          duration: formatClock(durationSec),
          limit: model.max_duration_sec,
        })
      );
    }

    row.status = "recognizing";
    const response = await JobAPI.transcribeJob(
      new Blob([wav], { type: "audio/wav" }),
      row.file.name,
      model.id
    );
    const job = response.data.data;
    if (!job) throw new Error(t("voice_workbench.job.unexpectedError"));
    if (job.status === "failed") {
      row.job = job;
      throw new Error(job.error || t("voice_workbench.job.recognizeFailed"));
    }

    row.job = job;
    row.status = "success";
    row.wav = undefined;
    selectedRowKey.value = row.key;
    void historyRef.value?.refresh();
    return job;
  } catch (error) {
    failRow(row, error);
    throw error;
  }
}

async function startBatch() {
  if (running.value) return;
  const model = selectedModel.value;
  if (!model) {
    ElMessage.warning(t("voice_workbench.job.modelRequired"));
    return;
  }

  const form = modelFormRef.value;
  if (form) {
    const valid = await (form.validate as () => Promise<boolean>)().catch(() => false);
    if (!valid) return;
  }

  const targets = uploadRows.value.filter(
    (row) => row.status === "pending" || row.status === "failed"
  );
  if (targets.length === 0) {
    ElMessage.warning(t("voice_workbench.job.noPending"));
    return;
  }

  running.value = true;
  try {
    const results = await runSequential(targets, (row) => transcribeRow(row, model));
    const failed = results.filter((item) => item.status === "failed").length;
    if (failed === 0) {
      ElMessage.success(t("voice_workbench.job.batchDone", { count: results.length }));
    } else {
      ElMessage.warning(
        t("voice_workbench.job.batchDoneWithFailures", {
          success: results.length - failed,
          failed,
        })
      );
    }
  } finally {
    running.value = false;
  }
}

async function retryRow(row: Record<PropertyKey, any>) {
  const target = asUploadRow(row);
  if (running.value || target.status !== "failed") return;
  const model = selectedModel.value;
  if (!model) {
    ElMessage.warning(t("voice_workbench.job.modelRequired"));
    return;
  }
  running.value = true;
  try {
    await runSequential([target], (item) => transcribeRow(item, model));
  } finally {
    running.value = false;
  }
}

const historyRef = ref<InstanceType<typeof HistoryPanel> | null>(null);
let firstLoad = true;

onMounted(async () => {
  await loadModelOptions();
  firstLoad = false;
});

// 从配置页返回时重新拉取可用模型（KeepAlive 下 mounted 不会重跑）
onActivated(() => {
  if (!firstLoad && !running.value) void loadModelOptions();
});
</script>

<style scoped>
/**
 * 三张卡片纵向堆叠在可滚动列里，必须保持自身高度。
 * `.fa-table-card` 的 `flex: 1` 会让卡片被压缩，而 `.el-card__body` 是
 * `overflow: hidden`，于是「开始转写 / 清空列表」等底部操作会被裁掉。
 */
.voice-job-card {
  flex: none;
}
</style>
