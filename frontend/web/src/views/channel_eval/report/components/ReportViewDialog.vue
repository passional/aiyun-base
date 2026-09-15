<!-- 渠道评估 - 报告视图：按模型分组对比图与明细 / 交叉对比 / 异常清单 / 自动结论 / 导出 HTML -->
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
      <template v-if="view">
        <!-- 报告头：所选运行与基准运行标识 -->
        <div class="mb-2 flex flex-wrap items-center gap-2">
          <ElTag v-for="run in view.runs" :key="run.id" size="small" type="primary">
            {{ runTagLabel(run) }}
          </ElTag>
          <ElTag v-if="view.baseline_run" size="small" type="success">
            {{ $t("channel_eval.report.baselineTag") }} · {{ runTagLabel(view.baseline_run) }}
          </ElTag>
          <span class="text-xs text-g-500">
            {{ $t("channel_eval.report.generatedAt") }}：{{ view.generated_at }}
          </span>
        </div>
        <div v-if="view.report.note" class="mb-2 rounded bg-g-50 p-2 text-sm">
          <span class="font-medium">{{ $t("channel_eval.report.note") }}：</span>
          <span class="whitespace-pre-wrap">{{ view.report.note }}</span>
        </div>

        <!-- 按模型分组：对比图 + 明细表 -->
        <template v-for="group in view.models" :key="group.model">
          <div class="mt-4 mb-2 flex items-center gap-2">
            <span class="text-sm font-medium">{{ group.model }}</span>
            <ElTag v-if="!group.has_baseline" size="small" type="info">
              {{ $t("channel_eval.report.noBaselineData") }}
            </ElTag>
          </div>
          <FaDualBarCompareChart
            :positive-data="chartData(group).positive"
            :negative-data="chartData(group).negative"
            :x-axis-data="chartData(group).labels"
            :positive-name="$t('channel_eval.run.ttft')"
            :negative-name="$t('channel_eval.run.totalMs')"
            :y-axis-min="-chartMax(group)"
            :y-axis-max="chartMax(group)"
            height="220px"
          />
          <FaTable
            :data="groupRows(group)"
            :columns="groupColumns"
            size="small"
            :empty-text="$t('channel_eval.report.noData')"
            :empty-height="'100px'"
          />
        </template>

        <!-- 交叉对比（客户端实测 vs 后台日志） -->
        <div class="mt-4 mb-2 text-sm font-medium">{{ $t("channel_eval.report.crossTitle") }}</div>
        <FaTable
          :data="crossRows"
          :columns="crossColumns"
          size="small"
          :empty-text="$t('channel_eval.report.noData')"
          :empty-height="'100px'"
        />

        <!-- 异常清单 -->
        <div class="mt-4 mb-2 text-sm font-medium">
          {{ $t("channel_eval.report.anomalyTitle") }}
        </div>
        <FaTable
          :data="view.anomalies"
          :columns="anomalyColumns"
          size="small"
          :empty-text="$t('channel_eval.report.anomalyEmpty')"
          :empty-height="'100px'"
        />

        <!-- 自动结论（仅阈值事实） -->
        <div class="mt-4 mb-2 text-sm font-medium">
          {{ $t("channel_eval.report.conclusionsTitle") }}
        </div>
        <ul v-if="view.conclusions.length" class="list-disc pl-5 text-sm">
          <li v-for="(item, index) in view.conclusions" :key="index">{{ item.content }}</li>
        </ul>
        <ElEmpty
          v-else
          :description="$t('channel_eval.report.conclusionsEmpty')"
          :image-size="60"
        />

        <!-- 指标口径说明 -->
        <div class="mt-4 rounded bg-g-50 p-2 text-xs text-g-500">
          {{ $t("channel_eval.report.metricNote") }}
        </div>
      </template>
      <ElEmpty v-else-if="!loading" :description="$t('channel_eval.report.loadFailed')" />
    </div>

    <template #footer>
      <ElButton @click="visible = false">{{ $t("common.close") }}</ElButton>
      <ElButton type="primary" :loading="exporting" @click="handleExport">
        {{ $t("channel_eval.report.exportHtml") }}
      </ElButton>
    </template>
  </FaDialog>
</template>

<script setup lang="ts">
import ReportAPI, {
  type ReportAnomalyItem,
  type ReportComboView,
  type ReportModelGroup,
  type ReportRunInfo,
  type ReportView,
} from "@/api/module_channel_eval/report";
import { makeStatusFormatter } from "@utils";
import type { ColumnOption } from "@/types/component";
import { saveAs } from "file-saver";

defineOptions({ name: "ReportViewDialog" });

const visible = defineModel<boolean>({ default: false });
const props = defineProps<{ reportId?: number }>();
const { t } = useI18n();

const view = ref<ReportView | null>(null);
const loading = ref(false);
const exporting = ref(false);

const dialogTitle = computed(() => {
  const base = t("channel_eval.report.viewTitle");
  return props.reportId ? `${base} #${props.reportId}` : base;
});

async function loadView() {
  if (!props.reportId) return;
  loading.value = true;
  try {
    const response = await ReportAPI.getReportView(props.reportId);
    view.value = response.data.data;
  } catch {
    // 请求失败已由拦截器提示
  } finally {
    loading.value = false;
  }
}

watch([visible, () => props.reportId], ([isVisible, reportId]) => {
  if (isVisible && reportId) {
    view.value = null;
    void loadView();
  }
});

// ─── 展示辅助 ───
function formatMs(value?: number | null) {
  return value == null ? "—" : String(Math.round(value));
}

function formatSignedMs(value?: number | null) {
  if (value == null) return "—";
  const rounded = Math.round(value);
  return `${rounded > 0 ? "+" : ""}${rounded}`;
}

function formatRate(value?: number | null) {
  return value == null ? "—" : `${(value * 100).toFixed(1)}%`;
}

function runTagLabel(run: ReportRunInfo) {
  return `#${run.id} ${run.platform_name || ""}`.trim();
}

function rowChannelLabel(row: ReportComboView) {
  const label = `#${row.channel_id} ${row.channel_name}`;
  return row.is_baseline ? `${label}（${t("channel_eval.report.baselineTag")}）` : label;
}

function groupRows(group: ReportModelGroup): ReportComboView[] {
  return [...(group.baseline ? [group.baseline] : []), ...group.combos];
}

function chartData(group: ReportModelGroup) {
  const rows = groupRows(group);
  return {
    labels: rows.map((row) => rowChannelLabel(row)),
    positive: rows.map((row) => row.ttft_avg_ms ?? 0),
    negative: rows.map((row) => row.total_avg_ms ?? 0),
  };
}

function chartMax(group: ReportModelGroup) {
  const values = groupRows(group).flatMap((row) => [row.ttft_avg_ms ?? 0, row.total_avg_ms ?? 0]);
  const max = Math.max(...values, 0);
  return Math.max(1, Math.ceil(max * 1.1));
}

// ─── 表格列 ───
const baselineMarkMap = () => ({
  true: { type: "success" as const, text: t("channel_eval.report.baselineTag") },
  false: { type: "info" as const, text: "—" },
});

const groupColumns = computed<ColumnOption<ReportComboView>[]>(() => [
  {
    prop: "is_baseline",
    label: t("channel_eval.report.mark"),
    width: 80,
    formatter: makeStatusFormatter<ReportComboView>({
      valueGetter: (row) => row.is_baseline,
      map: baselineMarkMap(),
    }),
  },
  {
    prop: "channel_model",
    label: t("channel_eval.run.channelModel"),
    minWidth: 200,
    showOverflowTooltip: true,
    formatter: (row) => rowChannelLabel(row),
  },
  {
    prop: "ttft_avg_ms",
    label: t("channel_eval.run.avgTtft"),
    minWidth: 120,
    formatter: (row) => formatMs(row.ttft_avg_ms),
  },
  {
    prop: "total_avg_ms",
    label: t("channel_eval.run.avgTotal"),
    minWidth: 120,
    formatter: (row) => formatMs(row.total_avg_ms),
  },
  {
    prop: "be_frt_avg_ms",
    label: t("channel_eval.report.beFrtAvg"),
    minWidth: 130,
    formatter: (row) => formatMs(row.be_frt_avg_ms),
  },
  {
    prop: "be_use_time_avg_ms",
    label: t("channel_eval.report.beUseTimeAvg"),
    minWidth: 140,
    formatter: (row) => formatMs(row.be_use_time_avg_ms),
  },
  { prop: "executed", label: t("channel_eval.report.sampleCount"), width: 86 },
  {
    prop: "success_rate",
    label: t("channel_eval.run.successRate"),
    width: 90,
    formatter: (row) => formatRate(row.success_rate),
  },
  { prop: "anomaly", label: t("channel_eval.run.anomalyCount"), width: 70 },
  {
    prop: "ttft_vs_baseline_ms",
    label: t("channel_eval.report.vsBaselineTtft"),
    minWidth: 140,
    formatter: (row) => formatSignedMs(row.ttft_vs_baseline_ms),
  },
  {
    prop: "total_vs_baseline_ms",
    label: t("channel_eval.report.vsBaselineTotal"),
    minWidth: 140,
    formatter: (row) => formatSignedMs(row.total_vs_baseline_ms),
  },
]);

const crossRows = computed<ReportComboView[]>(() => {
  const rows: ReportComboView[] = [];
  for (const group of view.value?.models ?? []) {
    rows.push(...groupRows(group));
  }
  return rows;
});

const crossColumns = computed<ColumnOption<ReportComboView>[]>(() => [
  {
    prop: "is_baseline",
    label: t("channel_eval.report.mark"),
    width: 80,
    formatter: makeStatusFormatter<ReportComboView>({
      valueGetter: (row) => row.is_baseline,
      map: baselineMarkMap(),
    }),
  },
  {
    prop: "channel_model",
    label: t("channel_eval.run.channelModel"),
    minWidth: 200,
    showOverflowTooltip: true,
    formatter: (row) => rowChannelLabel(row),
  },
  {
    prop: "ttft_avg_ms",
    label: t("channel_eval.run.avgTtft"),
    minWidth: 120,
    formatter: (row) => formatMs(row.ttft_avg_ms),
  },
  {
    prop: "be_frt_avg_ms",
    label: t("channel_eval.report.beFrtAvg"),
    minWidth: 130,
    formatter: (row) => formatMs(row.be_frt_avg_ms),
  },
  {
    prop: "cross_ttft_diff_ms",
    label: t("channel_eval.report.crossTtftDiff"),
    minWidth: 150,
    formatter: (row) => formatSignedMs(row.cross_ttft_diff_ms),
  },
  {
    prop: "total_avg_ms",
    label: t("channel_eval.run.avgTotal"),
    minWidth: 120,
    formatter: (row) => formatMs(row.total_avg_ms),
  },
  {
    prop: "be_use_time_avg_ms",
    label: t("channel_eval.report.beUseTimeAvg"),
    minWidth: 140,
    formatter: (row) => formatMs(row.be_use_time_avg_ms),
  },
  {
    prop: "cross_total_diff_ms",
    label: t("channel_eval.report.crossTotalDiff"),
    minWidth: 150,
    formatter: (row) => formatSignedMs(row.cross_total_diff_ms),
  },
]);

const anomalyColumns = computed<ColumnOption<ReportAnomalyItem>[]>(() => [
  {
    prop: "kind",
    label: t("channel_eval.report.anomalyKind"),
    width: 90,
    formatter: makeStatusFormatter<ReportAnomalyItem>({
      valueGetter: (row) => row.kind,
      map: {
        anomaly: { type: "warning", text: t("channel_eval.report.kindAnomaly") },
        failed: { type: "danger", text: t("channel_eval.report.kindFailed") },
      },
    }),
  },
  {
    prop: "model",
    label: t("channel_eval.report.model"),
    minWidth: 150,
    showOverflowTooltip: true,
  },
  {
    prop: "channel",
    label: t("channel_eval.report.channel"),
    minWidth: 190,
    showOverflowTooltip: true,
    formatter: (row) => `#${row.channel_id} ${row.channel_name}`,
  },
  { prop: "seq", label: t("channel_eval.run.sampleSeq"), width: 70 },
  {
    prop: "is_warmup",
    label: t("channel_eval.run.warmup"),
    width: 84,
    formatter: makeStatusFormatter<ReportAnomalyItem>({
      valueGetter: (row) => row.is_warmup,
      map: {
        true: { type: "warning", text: t("channel_eval.run.warmupTag") },
        false: { type: "info", text: "—" },
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
    formatter: makeStatusFormatter<ReportAnomalyItem>({
      valueGetter: (row) => row.be_matched,
      map: {
        true: { type: "success", text: t("channel_eval.run.beMatched") },
        false: { type: "warning", text: t("channel_eval.run.beNoRecord") },
      },
    }),
  },
]);

// ── 导出 HTML（自包含、导出即冻结） ───
function parseFilename(disposition: string | undefined, fallback: string) {
  if (!disposition) return fallback;
  const match = disposition.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i);
  return match?.[1] ? decodeURIComponent(match[1]) : fallback;
}

async function handleExport() {
  if (!props.reportId) return;
  exporting.value = true;
  try {
    const response = await ReportAPI.exportReport(props.reportId);
    const blob = new Blob([response.data], { type: "text/html;charset=utf-8" });
    const filename = parseFilename(
      response.headers?.["content-disposition"],
      `channel-eval-report-${props.reportId}.html`
    );
    saveAs(blob, filename);
  } catch {
    // 请求失败已由拦截器提示
  } finally {
    exporting.value = false;
  }
}
</script>
