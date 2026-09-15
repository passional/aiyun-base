<!-- 渠道评估 - 运行详情：状态流转 / SSE 进度 / 组合聚合 / 样本明细（客户端实测 + 后台配对） -->
<template>
  <FaDialog
    v-model="visible"
    :title="dialogTitle"
    width="1100px"
    dialog-class="crud-embed-dialog"
    modal-class="crud-embed-dialog"
    @close="visible = false"
  >
    <div v-loading="loading">
      <template v-if="detail">
        <FaDescriptions
          :column="3"
          :data="summaryDisplay"
          :items="summaryItems"
          label-width="100px"
          :max-height="'none'"
        >
          <template #status>
            <FaStatusTag
              :type="runStatusTagType(currentStatus)"
              :label="runStatusText(currentStatus)"
            />
          </template>
        </FaDescriptions>

        <div class="mt-3 mb-2 text-sm font-medium">{{ $t("channel_eval.run.paramsSnapshot") }}</div>
        <FaDescriptions
          :column="3"
          :data="paramsDisplay"
          :items="snapshotItems"
          label-width="100px"
          :max-height="'none'"
        />

        <!-- 执行进度（SSE 实时，断线自动重连） -->
        <div v-if="isActive" class="mt-3 rounded bg-g-50 p-3">
          <div class="mb-2 flex flex-wrap items-center gap-2">
            <span class="text-sm font-medium">{{ $t("channel_eval.run.progressTitle") }}</span>
            <ElTag size="small" :type="sseConnected ? 'success' : 'info'">
              {{
                sseConnected
                  ? $t("channel_eval.run.sseConnected")
                  : $t("channel_eval.run.sseDisconnected")
              }}
            </ElTag>
          </div>
          <ElProgress :percentage="progressPercent" :stroke-width="10" />
          <div class="mt-1 text-sm">
            <template v-if="progress">
              {{
                $t("channel_eval.run.progressCompleted", {
                  done: progress.completed_combos ?? 0,
                  total: progress.total_combos ?? detail.combo_count,
                })
              }}
            </template>
            <template v-else>{{ $t("channel_eval.run.waiting") }}</template>
            <span v-if="progress?.current_combo">
              · {{ $t("channel_eval.run.progressCurrent", { combo: progress.current_combo }) }}
            </span>
          </div>
          <div v-if="progress?.message" class="mt-1 text-xs text-g-500">
            {{ progress.message }}
          </div>
        </div>

        <!-- 组合聚合 -->
        <div class="mt-4 mb-2 text-sm font-medium">{{ $t("channel_eval.run.combosTitle") }}</div>
        <FaTable
          :data="detail.combos"
          :columns="comboColumns"
          size="small"
          :empty-text="$t('channel_eval.run.combosEmpty')"
          :empty-height="'120px'"
        />

        <!-- 样本明细 -->
        <div class="mt-4 mb-2 text-sm font-medium">{{ $t("channel_eval.run.samplesTitle") }}</div>
        <FaTable
          :data="detail.samples"
          :columns="sampleColumns"
          size="small"
          :empty-text="$t('channel_eval.run.samplesEmpty')"
          :empty-height="'120px'"
        />
      </template>
      <ElEmpty v-else-if="!loading" :description="$t('channel_eval.run.detailLoadFailed')" />
    </div>

    <template #footer>
      <ElButton @click="visible = false">{{ $t("common.close") }}</ElButton>
      <ElButton v-if="canCancel" type="warning" plain :loading="cancelling" @click="handleCancel">
        {{ $t("channel_eval.run.cancelRun") }}
      </ElButton>
      <ElButton type="primary" :loading="loading" @click="loadDetail">
        {{ $t("channel_eval.run.refresh") }}
      </ElButton>
    </template>
  </FaDialog>
</template>

<script setup lang="ts">
import RunAPI, {
  RunStream,
  type RunComboAggregate,
  type RunDetail,
  type RunProgress,
  type RunSample,
} from "@/api/module_channel_eval/run";
import { makeStatusFormatter } from "@utils";
import type { ColumnOption } from "@/types/component";
import type { DescriptionsItem } from "@/components/display/fa-descriptions/index.vue";
import { runStatusTagType, runStatusText } from "../status";

defineOptions({ name: "RunDetailDialog" });

const visible = defineModel<boolean>({ default: false });
const props = defineProps<{ runId?: number }>();
const { t } = useI18n();

const detail = ref<RunDetail | null>(null);
const loading = ref(false);
const cancelling = ref(false);
const progress = ref<RunProgress | null>(null);
const sseConnected = ref(false);

let reloadTimer: ReturnType<typeof setTimeout> | null = null;
let lastSignature = "";

const stream = new RunStream({
  onProgress: (payload) => {
    progress.value = payload;
    const signature = [
      payload.status,
      payload.completed_combos ?? "",
      payload.current_sample ?? "",
      payload.current_combo ?? "",
      payload.message ?? "",
    ].join("|");
    if (signature !== lastSignature) {
      lastSignature = signature;
      scheduleReload(payload.status);
    }
  },
  onStatus: (connected) => {
    sseConnected.value = connected;
  },
});

const currentStatus = computed(() => {
  const detailStatus = detail.value?.status;
  // 终态以详情（DB 权威状态）为准：SSE 进度可能滞留旧值，不能覆盖已终态的按钮/展示
  if (detailStatus && ["success", "failed", "cancelled"].includes(detailStatus))
    return detailStatus;
  return progress.value?.status ?? detailStatus ?? "pending";
});
const isActive = computed(() => ["pending", "running", "cancelling"].includes(currentStatus.value));
// 与后端 cancel 契约一致：仅 pending/running 可取消（cancelling 已请求过，再次点击会被 400 拒绝）
const canCancel = computed(() => ["pending", "running"].includes(currentStatus.value));

const progressPercent = computed(() => {
  const total = progress.value?.total_combos ?? detail.value?.combo_count ?? 0;
  const done = progress.value?.completed_combos ?? 0;
  if (!total) return 0;
  return Math.min(100, Math.round((done / total) * 100));
});

const dialogTitle = computed(() => {
  const base = t("channel_eval.run.detailTitle");
  return detail.value?.id ? `${base} #${detail.value.id}` : base;
});

// ─── 概要 / 参数快照展示 ───
const summaryDisplay = computed<Record<string, unknown>>(() => {
  const data = detail.value;
  if (!data) return {};
  return {
    platform_name: data.platform_name || "—",
    status: runStatusText(currentStatus.value),
    combo_count: data.combo_count,
    started_at: data.started_at || "—",
    finished_at: data.finished_at || "—",
    error: data.error || "—",
  };
});

const summaryItems = computed<DescriptionsItem[]>(() => [
  { label: t("channel_eval.run.platform"), prop: "platform_name" },
  { label: t("channel_eval.common.status"), prop: "status", slot: "status" },
  { label: t("channel_eval.run.comboCount"), prop: "combo_count" },
  { label: t("channel_eval.run.startedAt"), prop: "started_at" },
  { label: t("channel_eval.run.finishedAt"), prop: "finished_at" },
  { label: t("channel_eval.run.errorInfo"), prop: "error", span: 3 },
]);

const paramsDisplay = computed<Record<string, unknown>>(() => {
  const params = detail.value?.params ?? {};
  return {
    request_count: params.request_count ?? "—",
    warmup:
      params.warmup == null
        ? "—"
        : params.warmup
          ? t("channel_eval.run.warmupOn")
          : t("channel_eval.run.warmupOff"),
    interval_seconds: params.interval_seconds ?? "—",
    timeout_seconds: params.timeout_seconds ?? "—",
    anomaly_threshold_seconds: params.anomaly_threshold_seconds ?? "—",
    prompt: params.prompt || "—",
  };
});

const snapshotItems = computed<DescriptionsItem[]>(() => [
  { label: t("channel_eval.run.paramRequestCount"), prop: "request_count" },
  { label: t("channel_eval.run.paramWarmup"), prop: "warmup" },
  { label: t("channel_eval.run.paramInterval"), prop: "interval_seconds" },
  { label: t("channel_eval.run.paramTimeout"), prop: "timeout_seconds" },
  { label: t("channel_eval.run.paramAnomaly"), prop: "anomaly_threshold_seconds" },
  { label: t("channel_eval.run.paramPrompt"), prop: "prompt", span: 3 },
]);

// ─── 组合 / 样本表格 ───
function formatMs(value?: number | null) {
  return value == null ? "—" : String(Math.round(value));
}

function formatRate(value?: number | null) {
  return value == null ? "—" : `${(value * 100).toFixed(1)}%`;
}

function formatTps(value?: number | null) {
  return value == null ? "—" : value.toFixed(1);
}

const comboById = computed(() => {
  const map = new Map<number, RunComboAggregate>();
  for (const combo of detail.value?.combos ?? []) map.set(combo.combo_id, combo);
  return map;
});

function comboLabel(row: RunComboAggregate) {
  return `#${row.channel_id} ${row.channel_name} × ${row.model}`;
}

function sampleComboLabel(row: RunSample) {
  const combo = comboById.value.get(row.combo_id);
  return combo ? comboLabel(combo) : `#${row.combo_id}`;
}

const comboColumns = computed<ColumnOption<RunComboAggregate>[]>(() => [
  {
    prop: "channel_model",
    label: t("channel_eval.run.channelModel"),
    minWidth: 230,
    showOverflowTooltip: true,
    formatter: (row) => comboLabel(row),
  },
  {
    prop: "status",
    label: t("channel_eval.run.comboStatus"),
    width: 96,
    formatter: makeStatusFormatter<RunComboAggregate>({
      valueGetter: (row) => row.status,
      map: {
        pending: { type: "info", text: t("channel_eval.run.statusPending") },
        running: { type: "primary", text: t("channel_eval.run.statusRunning") },
        success: { type: "success", text: t("channel_eval.run.statusSuccess") },
        failed: { type: "danger", text: t("channel_eval.run.statusFailed") },
        cancelled: { type: "info", text: t("channel_eval.run.statusCancelled") },
      },
    }),
  },
  { prop: "planned", label: t("channel_eval.run.planned"), width: 70 },
  { prop: "executed", label: t("channel_eval.run.executed"), width: 80 },
  { prop: "success", label: t("channel_eval.run.successCount"), width: 70 },
  { prop: "failed", label: t("channel_eval.run.failedCount"), width: 70 },
  { prop: "anomaly", label: t("channel_eval.run.anomalyCount"), width: 70 },
  {
    prop: "success_rate",
    label: t("channel_eval.run.successRate"),
    width: 90,
    formatter: (row) => formatRate(row.success_rate),
  },
  {
    prop: "ttft_avg_ms",
    label: t("channel_eval.run.avgTtft"),
    minWidth: 110,
    formatter: (row) => formatMs(row.ttft_avg_ms),
  },
  {
    prop: "total_avg_ms",
    label: t("channel_eval.run.avgTotal"),
    minWidth: 110,
    formatter: (row) => formatMs(row.total_avg_ms),
  },
  {
    prop: "tps_avg",
    label: t("channel_eval.run.avgTps"),
    minWidth: 120,
    formatter: (row) => formatTps(row.tps_avg),
  },
  {
    prop: "with_anomaly",
    label: t("channel_eval.run.withAnomalyAvg"),
    minWidth: 140,
    formatter: (row) =>
      `${formatMs(row.ttft_with_anomaly_avg_ms)} / ${formatMs(row.total_with_anomaly_avg_ms)}`,
  },
  {
    prop: "error",
    label: t("channel_eval.run.comboError"),
    minWidth: 160,
    showOverflowTooltip: true,
    formatter: (row) => row.error || "—",
  },
]);

const sampleColumns = computed<ColumnOption<RunSample>[]>(() => [
  {
    prop: "combo",
    label: t("channel_eval.run.channelModel"),
    minWidth: 210,
    showOverflowTooltip: true,
    formatter: (row) => sampleComboLabel(row),
  },
  {
    prop: "seq",
    label: t("channel_eval.run.sampleSeq"),
    width: 70,
    formatter: (row) => (row.is_warmup ? "—" : String(row.seq)),
  },
  {
    prop: "is_warmup",
    label: t("channel_eval.run.warmup"),
    width: 84,
    formatter: makeStatusFormatter<RunSample>({
      valueGetter: (row) => row.is_warmup,
      map: {
        true: { type: "warning", text: t("channel_eval.run.warmupTag") },
        false: { type: "info", text: "—" },
      },
    }),
  },
  {
    prop: "status",
    label: t("channel_eval.run.sampleStatus"),
    width: 90,
    formatter: makeStatusFormatter<RunSample>({
      valueGetter: (row) => row.status,
      map: {
        success: { type: "success", text: t("channel_eval.run.sampleSuccess") },
        failed: { type: "danger", text: t("channel_eval.run.sampleFailed") },
      },
    }),
  },
  {
    prop: "ttft_ms",
    label: t("channel_eval.run.ttft"),
    width: 120,
    formatter: (row) => formatMs(row.ttft_ms),
  },
  {
    prop: "total_ms",
    label: t("channel_eval.run.totalMs"),
    width: 110,
    formatter: (row) => formatMs(row.total_ms),
  },
  {
    prop: "tokens_in",
    label: t("channel_eval.run.tokensIn"),
    width: 100,
    formatter: (row) => row.tokens_in ?? "—",
  },
  {
    prop: "tokens_out",
    label: t("channel_eval.run.tokensOut"),
    width: 100,
    formatter: (row) => row.tokens_out ?? "—",
  },
  {
    prop: "tps",
    label: t("channel_eval.run.tps"),
    width: 120,
    formatter: (row) => formatTps(row.tps),
  },
  {
    prop: "request_id",
    label: t("channel_eval.run.requestId"),
    minWidth: 150,
    showOverflowTooltip: true,
    formatter: (row) => row.request_id || "—",
  },
  {
    prop: "started_at",
    label: t("channel_eval.run.startedAtShort"),
    width: 165,
    showOverflowTooltip: true,
    formatter: (row) => row.started_at || "—",
  },
  {
    prop: "error",
    label: t("channel_eval.run.sampleError"),
    minWidth: 150,
    showOverflowTooltip: true,
    formatter: (row) => row.error || "—",
  },
  {
    prop: "be_matched",
    label: t("channel_eval.run.beSection"),
    width: 110,
    formatter: makeStatusFormatter<RunSample>({
      valueGetter: (row) => row.be_matched,
      map: {
        true: { type: "success", text: t("channel_eval.run.beMatched") },
        false: { type: "warning", text: t("channel_eval.run.beNoRecord") },
      },
    }),
  },
  {
    prop: "be_frt_ms",
    label: t("channel_eval.run.beFrt"),
    width: 120,
    formatter: (row) => formatMs(row.be_frt_ms),
  },
  {
    prop: "be_use_time_ms",
    label: t("channel_eval.run.beUseTime"),
    width: 130,
    formatter: (row) => formatMs(row.be_use_time_ms),
  },
  {
    prop: "be_tokens_in",
    label: t("channel_eval.run.beTokensIn"),
    width: 120,
    formatter: (row) => row.be_tokens_in ?? "—",
  },
  {
    prop: "be_tokens_out",
    label: t("channel_eval.run.beTokensOut"),
    width: 120,
    formatter: (row) => row.be_tokens_out ?? "—",
  },
  {
    prop: "be_cost",
    label: t("channel_eval.run.beCost"),
    width: 110,
    formatter: (row) => row.be_cost ?? "—",
  },
  {
    prop: "be_channel_id",
    label: t("channel_eval.run.beChannel"),
    width: 100,
    formatter: (row) => row.be_channel_id ?? "—",
  },
]);

// ─── 数据加载 / SSE 生命周期 ───
async function loadDetail() {
  if (!props.runId) return;
  loading.value = true;
  try {
    const response = await RunAPI.detailRun(props.runId);
    detail.value = response.data.data;
    // 详情已是终态：丢弃可能滞后的 SSE 进度，避免界面回到「执行中」
    if (["success", "failed", "cancelled"].includes(detail.value.status)) progress.value = null;
    if (!isActive.value) stopStream();
  } catch {
    // 请求失败已由拦截器提示
  } finally {
    loading.value = false;
  }
}

function scheduleReload(status: string) {
  if (reloadTimer) clearTimeout(reloadTimer);
  if (["success", "failed", "cancelled"].includes(status)) {
    stopStream();
    void loadDetail();
    return;
  }
  reloadTimer = setTimeout(() => {
    reloadTimer = null;
    void loadDetail();
  }, 600);
}

function startStream() {
  if (props.runId && isActive.value) stream.connect(props.runId);
}

function stopStream() {
  if (reloadTimer) {
    clearTimeout(reloadTimer);
    reloadTimer = null;
  }
  stream.disconnect();
  sseConnected.value = false;
}

watch([visible, () => props.runId], async ([isVisible, runId]) => {
  if (isVisible && runId) {
    lastSignature = "";
    progress.value = null;
    sseConnected.value = false;
    detail.value = null;
    await loadDetail();
    startStream();
  } else if (!isVisible) {
    stopStream();
  }
});

// KeepAlive 页面：失活释放 SSE，恢复时重连并刷新（任务 8.2 验收点）
onDeactivated(() => stopStream());
onActivated(() => {
  if (visible.value) {
    void loadDetail();
    startStream();
  }
});
onUnmounted(() => stopStream());

async function handleCancel() {
  if (!detail.value?.id) return;
  try {
    await confirmAction(t("channel_eval.run.cancelConfirm", { id: detail.value.id }));
    cancelling.value = true;
    await RunAPI.cancelRun(detail.value.id);
    await loadDetail();
  } catch {
    // 用户取消或请求失败（拦截器已提示）
  } finally {
    cancelling.value = false;
  }
}
</script>
