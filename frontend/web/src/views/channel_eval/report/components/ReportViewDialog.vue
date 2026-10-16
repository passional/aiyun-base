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

        <!-- 关键指标概览 -->
        <div class="mt-3 grid grid-cols-5 gap-2">
          <div v-for="card in kpiCards" :key="card.label" class="rounded bg-g-50 p-2">
            <div class="text-xs text-g-500">{{ card.label }}</div>
            <div class="text-base font-medium">{{ card.value }}</div>
            <div class="text-xs text-g-500">{{ card.foot }}</div>
          </div>
        </div>

        <!-- 模型对齐：未确认归属的模型独立成行、绝不隐藏 -->
        <div v-if="hasAlignment" class="mt-3 rounded bg-g-50 p-2 text-xs">
          <div class="flex flex-wrap items-center gap-2">
            <span class="font-medium">{{ $t("channel_eval.report.alignmentTitle") }}</span>
            <ElTag v-if="view.alignment.unmapped_count" size="small" type="warning">
              {{
                $t("channel_eval.report.unmappedCount", { count: view.alignment.unmapped_count })
              }}
            </ElTag>
            <ElTag v-else size="small" type="success">
              {{ $t("channel_eval.report.allAligned") }}
            </ElTag>
          </div>
          <ul v-if="view.alignment.notes.length" class="mt-1 list-disc pl-5 text-g-500">
            <li v-for="(note, index) in view.alignment.notes" :key="index">{{ note }}</li>
          </ul>
          <div v-if="view.alignment.mappings.length" class="mt-1 text-g-500">
            {{ $t("channel_eval.report.mappingsLabel") }}
            <ElTag
              v-for="item in view.alignment.mappings"
              :key="item.alias"
              size="small"
              class="ml-1"
            >
              {{ item.alias }} → {{ item.canonical }}
            </ElTag>
          </div>
        </div>

        <!-- 可比性提醒（仅陈述参数差异事实） -->
        <div v-if="view.comparable_notes.length" class="mt-3">
          <div class="mb-1 text-sm font-medium">
            {{ $t("channel_eval.report.comparableTitle") }}
          </div>
          <div
            v-for="(note, index) in view.comparable_notes"
            :key="index"
            class="mb-1 flex items-start gap-2 rounded bg-g-50 p-2 text-xs"
          >
            <ElTag size="small" :type="note.level === 'warning' ? 'warning' : 'info'">
              {{
                note.level === "warning"
                  ? $t("channel_eval.report.comparableWarning")
                  : $t("channel_eval.report.comparableInfo")
              }}
            </ElTag>
            <span>{{ note.content }}</span>
          </div>
        </div>

        <!-- 系列筛选 -->
        <div v-if="families.length > 1" class="mt-3 flex items-center gap-2">
          <span class="text-xs text-g-500">{{ $t("channel_eval.report.familyFilter") }}</span>
          <ElSelect v-model="familyFilter" size="small" style="width: 160px">
            <ElOption :value="'all'" :label="$t('channel_eval.report.familyAll')" />
            <ElOption v-for="item in families" :key="item" :value="item" :label="item" />
          </ElSelect>
        </div>

        <!-- 按规范模型分组：对比图 + 明细表 -->
        <template v-for="group in visibleGroups" :key="group.model_key">
          <div class="mt-4 mb-2 flex flex-wrap items-center gap-2">
            <span class="text-sm font-medium">{{ group.model }}</span>
            <ElTag v-if="group.family" size="small">{{ group.family }}</ElTag>
            <ElTag v-if="group.alignment === 'aligned'" size="small" type="success">
              {{ $t("channel_eval.report.alignedTag") }}
            </ElTag>
            <ElTag v-else-if="group.alignment === 'unmapped'" size="small" type="warning">
              {{ $t("channel_eval.report.unmappedTag") }}
            </ElTag>
            <ElTag v-if="group.raw_models.length > 1" size="small" type="info">
              {{ $t("channel_eval.report.namingDiffTag") }}
            </ElTag>
            <ElTag v-if="!group.has_baseline" size="small" type="info">
              {{ $t("channel_eval.report.noBaselineData") }}
            </ElTag>
            <span v-if="group.alignment_note" class="text-xs text-g-500">
              {{ group.alignment_note }}
            </span>
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

        <!-- 测试配置与执行信息 -->
        <div class="mt-4 mb-2 text-sm font-medium">
          {{ $t("channel_eval.report.runConfigTitle") }}
        </div>
        <div class="grid grid-cols-2 gap-3">
          <div
            v-for="config in view.run_configs"
            :key="config.run_id"
            class="rounded bg-g-50 p-3 text-xs"
          >
            <div class="mb-2 flex flex-wrap items-center gap-2 text-sm font-medium">
              #{{ config.run_id }} {{ config.platform_name || "—" }}
              <ElTag v-if="config.is_baseline" size="small" type="success">
                {{ $t("channel_eval.report.baselineTag") }}
              </ElTag>
              <ElTag size="small">{{ config.status }}</ElTag>
            </div>
            <div class="grid grid-cols-2 gap-x-3 gap-y-1">
              <span class="text-g-500">{{ $t("channel_eval.report.cfgWindow") }}</span>
              <span>{{ config.started_at || "—" }} ~ {{ config.finished_at || "—" }}</span>
              <span class="text-g-500">{{ $t("channel_eval.report.cfgComboCount") }}</span>
              <span>{{ config.combo_count }} × {{ config.request_count ?? "—" }}</span>
              <span class="text-g-500">{{ $t("channel_eval.run.warmup") }}</span>
              <span>{{ warmupLabel(config.warmup) }}</span>
              <span class="text-g-500">{{ $t("channel_eval.report.cfgIntervalTimeout") }}</span>
              <span
                >{{ config.interval_seconds ?? "—" }} / {{ config.timeout_seconds ?? "—" }}</span
              >
              <span class="text-g-500">
                {{ $t("channel_eval.report.cfgAnomalyThreshold") }}
              </span>
              <span>{{ config.anomaly_threshold_seconds ?? "—" }}</span>
              <span class="text-g-500">{{ $t("channel_eval.report.cfgResult") }}</span>
              <span>{{ config.success }} / {{ config.failed }} / {{ config.anomaly }}</span>
            </div>
            <div v-if="config.prompt" class="mt-2 whitespace-pre-wrap rounded bg-white p-2">
              {{ config.prompt }}
            </div>
          </div>
        </div>

        <!-- 指标口径说明（由视图数据自动生成，含本次实际生效的映射关系） -->
        <div class="mt-4 mb-2 text-sm font-medium">
          {{ $t("channel_eval.report.metricNoteTitle") }}
        </div>
        <ul class="list-disc pl-5 text-xs text-g-500">
          <li v-for="(note, index) in view.caliber_notes" :key="index">{{ note }}</li>
        </ul>
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
    familyFilter.value = "all";
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

function warmupLabel(value?: boolean | null) {
  if (value == null) return t("channel_eval.report.warmupUnknown");
  return value ? t("channel_eval.report.warmupOn") : t("channel_eval.report.warmupOff");
}

// ─── 关键指标概览 / 对齐 / 系列筛选 ───
const kpiCards = computed(() => {
  const current = view.value;
  if (!current) return [];
  const rows = current.models.flatMap((group) => groupRows(group));
  const sum = (pick: (row: ReportComboView) => number | null | undefined) =>
    rows.reduce((total, row) => total + (pick(row) ?? 0), 0);
  const planned = sum((row) => row.planned);
  const success = sum((row) => row.success);
  const anomaly = sum((row) => row.anomaly);
  const failed = sum((row) => row.failed);
  const unmapped = current.alignment.unmapped_count;
  return [
    {
      label: t("channel_eval.report.kpiModels"),
      value: String(current.models.length),
      foot: unmapped
        ? t("channel_eval.report.kpiUnmapped", { count: unmapped })
        : t("channel_eval.report.kpiAllAligned"),
    },
    {
      label: t("channel_eval.report.kpiCombos"),
      value: String(rows.length),
      foot: t("channel_eval.report.kpiChannelModel"),
    },
    {
      label: t("channel_eval.report.kpiSamples"),
      value: String(sum((row) => row.executed)),
      foot: t("channel_eval.report.kpiNonWarmup"),
    },
    {
      label: t("channel_eval.report.kpiSuccessRate"),
      value: planned ? `${((100 * success) / planned).toFixed(1)}%` : "—",
      foot: t("channel_eval.report.kpiPlanned", { success, planned }),
    },
    {
      label: t("channel_eval.report.kpiAnomalyFailed"),
      value: `${anomaly} / ${failed}`,
      foot: t("channel_eval.report.kpiAnomalyFailedFoot", { anomaly, failed }),
    },
  ];
});

const hasAlignment = computed(() => {
  const current = view.value;
  if (!current) return false;
  return Boolean(
    current.alignment.unmapped_count ||
    current.alignment.mappings.length ||
    current.alignment.notes.length
  );
});

const families = computed(() =>
  Array.from(
    new Set(view.value?.models.map((group) => group.family).filter(Boolean) as string[])
  ).sort()
);

const familyFilter = ref<string>("all");

const visibleGroups = computed(() => {
  const groups = view.value?.models ?? [];
  return familyFilter.value === "all"
    ? groups
    : groups.filter((group) => group.family === familyFilter.value);
});

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
