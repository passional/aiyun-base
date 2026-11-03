<template>
  <div class="flex min-h-0 flex-col">
    <ElEmpty v-if="!job" :description="t('voice_workbench.job.selectResultHint')" />

    <div v-else class="flex min-h-0 flex-col gap-3">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <div class="min-w-0">
          <div class="truncate text-sm font-medium">{{ filename || job.original_filename }}</div>
          <div class="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-(--el-text-color-secondary)">
            <span>{{ formatElapsed(job.elapsed_ms) }}</span>
            <span v-if="job.duration_sec > 0">{{ formatClock(job.duration_sec) }}</span>
            <FaStatusTag :type="statusTagType(job.status)" :label="statusLabel(job.status)" />
          </div>
        </div>
        <div class="flex flex-wrap items-center gap-2">
          <FaCopyButton v-if="transcript" :text="transcript">
            {{ t("voice_workbench.job.copyAll") }}
          </FaCopyButton>
          <ElButton v-if="transcript" size="small" :icon="Download" @click="exportText">
            {{ t("voice_workbench.job.exportTxt") }}
          </ElButton>
        </div>
      </div>

      <ElAlert
        v-if="job.status === 'failed'"
        type="error"
        :closable="false"
        show-icon
        :title="job.error || t('voice_workbench.job.recognizeFailed')"
      />

      <div v-if="transcript" class="whitespace-pre-wrap break-words rounded-xl bg-(--el-fill-color-light) p-3 text-sm leading-6">
        {{ transcript }}
      </div>
      <div v-else-if="job.status !== 'failed'" class="text-sm text-(--el-text-color-secondary)">
        {{ t("voice_workbench.job.noText") }}
      </div>

      <div v-if="segments.length > 0">
        <div class="mb-2 text-sm font-medium">{{ t("voice_workbench.job.segments") }}</div>
        <div class="space-y-2">
          <div
            v-for="(segment, index) in segments"
            :key="index"
            class="rounded-lg border border-(--el-border-color) p-2 text-sm"
          >
            <div class="mb-1 font-mono text-xs text-(--el-text-color-secondary)">
              {{ formatTimestamp(segment.start_time) }} - {{ formatTimestamp(segment.end_time) }}
            </div>
            <div class="whitespace-pre-wrap break-words">{{ segment.text }}</div>
          </div>
        </div>
      </div>
      <div v-else-if="transcript" class="text-xs text-(--el-text-color-secondary)">
        {{ t("voice_workbench.job.noSegments") }}
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { Download } from "@element-plus/icons-vue";
import { saveAs } from "file-saver";
import { ElMessage } from "element-plus";
import type { JobStatus, JobTable, TranscriptionSegment } from "@/api/module_voice_workbench/job";

defineOptions({
  name: "VoiceWorkbenchResultPanel",
  inheritAttrs: false,
});

const props = withDefaults(
  defineProps<{
    job?: JobTable | null;
    /** 导出文件名取原始音频名，而不是转码后的临时名 */
    filename?: string | null;
  }>(),
  { job: null, filename: null }
);

const { t } = useI18n();

const transcript = computed(() => props.job?.text?.trim() ?? "");
const segments = computed<TranscriptionSegment[]>(() => props.job?.segments ?? []);

const STATUS_I18N: Record<JobStatus, string> = {
  running: "statusRecognizing",
  success: "statusSuccess",
  failed: "statusFailed",
};

function statusLabel(status: JobStatus) {
  return t(`voice_workbench.job.${STATUS_I18N[status]}`);
}

function statusTagType(status: JobStatus) {
  if (status === "success") return "success";
  if (status === "failed") return "danger";
  return "warning";
}

function formatElapsed(ms?: number | null) {
  if (ms == null) return "—";
  return `${(ms / 1000).toFixed(1)}s`;
}

function formatClock(seconds?: number | null) {
  if (seconds == null) return "—";
  const total = Math.max(0, Math.round(seconds));
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  return `${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}`;
}

/** 分段时间为毫秒；有小时时才补出小时段 */
function formatTimestamp(ms?: number | null) {
  if (ms == null) return "—";
  const total = Math.max(0, Math.round(ms));
  const hours = Math.floor(total / 3_600_000);
  const minutes = Math.floor((total % 3_600_000) / 60_000);
  const seconds = Math.floor((total % 60_000) / 1000);
  const millis = total % 1000;
  const body = `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}.${String(millis).padStart(3, "0")}`;
  return hours > 0 ? `${String(hours).padStart(2, "0")}:${body}` : body;
}

function exportName() {
  const original = props.filename || props.job?.original_filename || "transcript";
  const base = original.replace(/\.[^./\\]+$/, "") || "transcript";
  return `${base}.txt`;
}

function exportText() {
  if (!transcript.value) return;
  saveAs(new Blob([transcript.value], { type: "text/plain;charset=utf-8" }), exportName());
  ElMessage.success(t("voice_workbench.job.exportStarted"));
}
</script>
