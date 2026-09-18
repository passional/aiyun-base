<!-- 渠道评估 - 测试运行：列表 / 新建向导 / 详情（SSE 进度、组合聚合、样本明细） -->
<template>
  <div class="fa-full-height">
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
          <FaTableHeaderLeft
            :perm-create="['channel_eval:run:add']"
            :create-loading="createLoading"
            @add="handleAdd"
          />
        </template>
      </FaTableHeader>

      <FaTable
        :loading="loading"
        :data="data"
        :columns="columns"
        :pagination="pagination"
        @pagination:size-change="handleSizeChange"
        @pagination:current-change="handleCurrentChange"
      />
    </ElCard>

    <RunCreateWizard v-model="wizardVisible" @created="handleCreated" />
    <RunDetailDialog v-model="detailVisible" :run-id="detailRunId" />
  </div>
</template>

<script setup lang="ts">
import RunAPI, {
  type RunPlatformOption,
  type RunStatus,
  type RunTable,
} from "@/api/module_channel_eval/run";
import { renderTableOperationCell, resolveStatusColumns, type TableOperationAction } from "@utils";
import { getColumnKey } from "@/hooks/core/useTableColumns";
import { runStatusMap } from "./status";
import type { ColumnOption } from "@/types/component";
import type { SearchFormItem } from "@/components/forms/fa-search-bar/index.vue";
import FaSearchBar from "@/components/forms/fa-search-bar/index.vue";
import FaTableHeader from "@/components/tables/fa-table-header/index.vue";
import RunCreateWizard from "./components/RunCreateWizard.vue";
import RunDetailDialog from "./components/RunDetailDialog.vue";

defineOptions({
  name: "ChannelEvalRun",
  inheritAttrs: false,
});

const { t, locale } = useI18n();

// ─── 搜索 ──
type RunSearchForm = {
  platform_id?: number;
  status?: RunStatus;
};

const searchForm = ref<RunSearchForm>({
  platform_id: undefined,
  status: undefined,
});

const showSearchBar = ref(true);
const searchBarRef = ref<InstanceType<typeof FaSearchBar> | null>(null);
const searchBarRules: Record<string, unknown> = {};

const platformOptions = ref<RunPlatformOption[]>([]);

const platformFilterOptions = computed(() =>
  platformOptions.value.map((item) => ({ label: item.name, value: item.id }))
);

const runStatusOptions = computed(() => [
  { label: t("channel_eval.run.statusPending"), value: "pending" },
  { label: t("channel_eval.run.statusRunning"), value: "running" },
  { label: t("channel_eval.run.statusCancelling"), value: "cancelling" },
  { label: t("channel_eval.run.statusSuccess"), value: "success" },
  { label: t("channel_eval.run.statusFailed"), value: "failed" },
  { label: t("channel_eval.run.statusCancelled"), value: "cancelled" },
]);

const searchItems = computed<SearchFormItem[]>(() => [
  {
    label: t("channel_eval.run.platform"),
    key: "platform_id",
    type: "select",
    props: {
      placeholder: t("channel_eval.run.platformFilterPlaceholder"),
      options: platformFilterOptions.value,
      clearable: true,
      filterable: true,
    },
    span: 6,
  },
  {
    label: t("channel_eval.common.status"),
    key: "status",
    type: "select",
    props: {
      placeholder: t("channel_eval.run.statusPlaceholder"),
      options: runStatusOptions.value,
      clearable: true,
    },
    span: 6,
  },
]);

// ─── 表格 ───
const ORDER_BY_DESC = JSON.stringify([{ id: "desc" }]);

function buildRunColumns(): ColumnOption<RunTable>[] {
  return [
    { prop: "id", label: "ID", width: 70 },
    {
      prop: "platform_name",
      label: t("channel_eval.run.platform"),
      minWidth: 130,
      showOverflowTooltip: true,
      formatter: (row) => row.platform_name || "—",
    },
    {
      prop: "status",
      label: t("channel_eval.common.status"),
      width: 96,
      status: runStatusMap(),
    },
    { prop: "combo_count", label: t("channel_eval.run.comboCount"), width: 84 },
    {
      prop: "params",
      label: t("channel_eval.run.requestCountShort"),
      width: 100,
      formatter: (row) => row.params?.request_count ?? "—",
    },
    {
      prop: "params_warmup",
      label: t("channel_eval.run.warmupShort"),
      width: 80,
      formatter: (row) =>
        row.params?.warmup == null
          ? "—"
          : row.params.warmup
            ? t("channel_eval.run.warmupOn")
            : t("channel_eval.run.warmupOff"),
    },
    {
      prop: "started_at",
      label: t("channel_eval.run.startedAt"),
      width: 168,
      showOverflowTooltip: true,
      formatter: (row) => row.started_at || "—",
    },
    {
      prop: "finished_at",
      label: t("channel_eval.run.finishedAt"),
      width: 168,
      showOverflowTooltip: true,
      formatter: (row) => row.finished_at || "—",
    },
    {
      prop: "error",
      label: t("channel_eval.run.errorInfo"),
      minWidth: 160,
      showOverflowTooltip: true,
      formatter: (row) => row.error || "—",
    },
    {
      prop: "operation",
      label: t("channel_eval.common.operation"),
      width: 170,
      fixed: "right",
      align: "center",
      formatter: (row) => formatRunOperationCell(row),
    },
  ];
}

const createLoading = ref(false);

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
  refreshUpdate,
  updateColumn,
} = useTable({
  core: {
    apiFn: RunAPI.listRun,
    apiParams: { page_no: 1, page_size: 10, order_by: ORDER_BY_DESC },
    columnsFactory: resolveStatusColumns<RunTable>(() => buildRunColumns()),
  },
});

// 语言切换后刷新列头文案（列配置工厂只在初始化时执行一次）
watch(locale, () => {
  updateColumn?.(buildRunColumns().map((col) => ({ prop: getColumnKey(col), updates: col })));
});

function buildRunReplaceParams(p: RunSearchForm): Record<string, unknown> {
  return {
    platform_id: p.platform_id,
    status: p.status,
    order_by: ORDER_BY_DESC,
  };
}

async function handleSearchBarSearch(params: RunSearchForm) {
  await searchBarRef.value?.validate?.();
  replaceSearchParams(buildRunReplaceParams(params));
  await getData();
}

async function onResetSearch() {
  searchForm.value = { platform_id: undefined, status: undefined };
  await resetSearchParams();
}

// ── 新建向导 / 详情 ───
const wizardVisible = ref(false);
const detailVisible = ref(false);
const detailRunId = ref<number | undefined>(undefined);

async function handleAdd() {
  createLoading.value = true;
  try {
    wizardVisible.value = true;
  } finally {
    createLoading.value = false;
  }
}

async function handleCreated(run: RunTable) {
  await refreshCreate();
  openDetail(run.id);
}

function openDetail(id?: number) {
  detailRunId.value = id;
  detailVisible.value = true;
}

// ─── 行操作 ───
function buildRunRowActions(row: RunTable): TableOperationAction[] {
  const actions: TableOperationAction[] = [
    {
      key: "detail",
      label: t("channel_eval.common.detail"),
      artType: "view",
      perm: "channel_eval:run:query",
      run: () => {
        openDetail(row.id);
      },
    },
  ];
  if (["pending", "running"].includes(row.status)) {
    actions.push({
      key: "cancel",
      label: t("channel_eval.run.cancelRun"),
      artType: "more",
      icon: "ri:stop-circle-line",
      perm: "channel_eval:run:cancel",
      run: () => {
        void cancelRun(row);
      },
    });
  }
  return actions;
}

function formatRunOperationCell(row: RunTable) {
  return renderTableOperationCell(buildRunRowActions(row), {
    wrapperClass: "inline-flex flex-wrap items-center justify-end gap-1",
  });
}

async function cancelRun(row: RunTable) {
  try {
    await confirmAction(t("channel_eval.run.cancelConfirm", { id: row.id }));
    await RunAPI.cancelRun(row.id!);
  } catch {
    // 用户取消或请求失败（拦截器已提示）
  } finally {
    // 无论成败都刷新：失败常因运行已结束（行数据滞后），刷新后取消入口即时收敛
    await refreshUpdate();
  }
}

// ── 执行中的运行：轮询刷新列表，避免行操作停留在旧状态（如已结束仍显示取消） ───
let pollTimer: ReturnType<typeof setTimeout> | null = null;

function stopActivePoll() {
  if (pollTimer) {
    clearTimeout(pollTimer);
    pollTimer = null;
  }
}

function scheduleActivePoll() {
  stopActivePoll();
  const hasActive = (data.value ?? []).some((row) =>
    ["pending", "running", "cancelling"].includes(row.status)
  );
  if (hasActive) {
    pollTimer = setTimeout(() => {
      pollTimer = null;
      void refreshData();
    }, 5000);
  }
}

watch(data, () => scheduleActivePoll());
onActivated(() => scheduleActivePoll());
onDeactivated(() => stopActivePoll());
onUnmounted(() => stopActivePoll());

onMounted(async () => {
  try {
    const response = await RunAPI.platformOptions(true);
    platformOptions.value = response.data.data ?? [];
  } catch {
    platformOptions.value = [];
  }
});
</script>
