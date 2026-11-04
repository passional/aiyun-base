<!--
  语音工作台 - 转写历史：分页列表 + 详情 + 单条/批量软删除。

  任务按创建者隔离由后端保证；本组件只消费当前用户可见的列表。
-->
<template>
  <div class="flex h-full min-h-0 flex-col">
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
              v-hasPerm="['voice_workbench:job:delete']"
              type="danger"
              plain
              :icon="Delete"
              :loading="batchDeleting"
              :disabled="selectedIds.length === 0"
              @click="handleBatchDelete"
            >
              {{
                selectedIds.length > 0
                  ? `${t("voice_workbench.common.batchDelete")} (${selectedIds.length})`
                  : t("voice_workbench.common.batchDelete")
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
        <template #original_filename="{ row }">
          <span class="break-all">{{ row.original_filename }}</span>
        </template>
        <template #status="{ row }">
          <FaStatusTag :type="statusTagType(row.status)" :label="statusLabel(row.status)" />
        </template>
      </FaTable>
    </ElCard>

    <FaDialog
      v-model="detailVisible"
      :title="t('voice_workbench.history.detailTitle')"
      width="820px"
      dialog-class="crud-embed-dialog"
      modal-class="crud-embed-dialog"
      form-mode="detail"
      @close="handleCloseDetail"
      @cancel="handleCloseDetail"
    >
      <ElScrollbar max-height="70vh">
        <FaDescriptions
          :column="2"
          :data="detailJob ?? {}"
          :items="detailItems"
          label-width="120px"
          :scrollbar="false"
        >
          <template #status="{ row }">
            <FaStatusTag
              :type="statusTagType(asJob(row)?.status)"
              :label="statusLabel(asJob(row)?.status)"
            />
          </template>
          <template #model_display_name="{ row }">
            {{ asJob(row)?.model_display_name || asJob(row)?.model_name || "—" }}
          </template>
          <template #file_size="{ row }">{{ formatBytes(asJob(row)?.file_size) }}</template>
          <template #duration_sec="{ row }">{{ formatClock(asJob(row)?.duration_sec) }}</template>
          <template #elapsed_ms="{ row }">{{ formatElapsed(asJob(row)?.elapsed_ms) }}</template>
          <template #error="{ row }">
            <span class="whitespace-pre-wrap break-words">{{ asJob(row)?.error || "—" }}</span>
          </template>
          <template #usage="{ row }">
            <span class="break-all">{{ usageText(asJob(row)) }}</span>
          </template>
        </FaDescriptions>

        <div class="mt-4">
          <ResultPanel :job="detailJob" :filename="detailJob?.original_filename" />
        </div>
      </ElScrollbar>
    </FaDialog>
  </div>
</template>

<script setup lang="ts">
import JobAPI, { type JobStatus, type JobTable } from "@/api/module_voice_workbench/job";
import { renderTableOperationCell, resolveStatusColumns, type TableOperationAction } from "@utils";
import { getColumnKey } from "@/hooks/core/useTableColumns";
import { Delete } from "@element-plus/icons-vue";
import type { ColumnOption } from "@/types/component";
import type { SearchFormItem } from "@/components/forms/fa-search-bar/index.vue";
import type { DescriptionsItem } from "@/components/display/fa-descriptions/index.vue";
import FaSearchBar from "@/components/forms/fa-search-bar/index.vue";
import FaDescriptions from "@/components/display/fa-descriptions/index.vue";
import FaTableHeader from "@/components/tables/fa-table-header/index.vue";
import FaTableHeaderLeft from "@/components/tables/fa-table-header-left/index.vue";
import ResultPanel from "./ResultPanel.vue";

defineOptions({
  name: "VoiceWorkbenchHistoryPanel",
  inheritAttrs: false,
});

const { t, locale } = useI18n();

const STATUS_I18N: Record<JobStatus, string> = {
  running: "statusRecognizing",
  success: "statusSuccess",
  failed: "statusFailed",
};

function statusLabel(status?: JobStatus | null) {
  if (!status) return "—";
  return t(`voice_workbench.job.${STATUS_I18N[status]}`);
}

function statusTagType(status?: JobStatus | null) {
  if (status === "success") return "success";
  if (status === "failed") return "danger";
  if (status === "running") return "warning";
  return "info";
}

function asJob(row: unknown): JobTable | null {
  return (row as JobTable | null) ?? null;
}

function formatBytes(value?: number | null) {
  if (value == null) return "—";
  if (value < 1024) return `${value} B`;
  const kb = value / 1024;
  if (kb < 1024) return `${kb.toFixed(1)} KB`;
  return `${(kb / 1024).toFixed(1)} MB`;
}

function formatClock(seconds?: number | null) {
  if (seconds == null) return "—";
  const total = Math.max(0, Math.round(seconds));
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  return `${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}`;
}

function formatElapsed(ms?: number | null) {
  if (ms == null) return "—";
  return `${(ms / 1000).toFixed(1)}s`;
}

function usageText(row?: JobTable | null) {
  return row?.usage ? JSON.stringify(row.usage) : "—";
}

type HistorySearchForm = {
  original_filename?: string;
  status?: JobStatus;
};

const searchForm = ref<HistorySearchForm>({
  original_filename: undefined,
  status: undefined,
});
const showSearchBar = ref(true);
const searchBarRef = ref<InstanceType<typeof FaSearchBar> | null>(null);
const searchBarRules: Record<string, unknown> = {};

const statusOptions = computed(() => [
  { label: t("voice_workbench.job.statusRecognizing"), value: "running" },
  { label: t("voice_workbench.job.statusSuccess"), value: "success" },
  { label: t("voice_workbench.job.statusFailed"), value: "failed" },
]);

const searchItems = computed<SearchFormItem[]>(() => [
  {
    label: t("voice_workbench.history.originalFilename"),
    key: "original_filename",
    type: "input",
    placeholder: t("voice_workbench.history.filenameSearchPlaceholder"),
    clearable: true,
    span: 12,
  },
  {
    label: t("voice_workbench.common.status"),
    key: "status",
    type: "select",
    props: {
      placeholder: t("voice_workbench.common.selectPlaceholder"),
      options: statusOptions.value,
      clearable: true,
    },
    span: 12,
  },
]);

const ORDER_BY_DESC = JSON.stringify([{ id: "desc" }]);

function buildJobColumns(): ColumnOption<JobTable>[] {
  return [
    { type: "selection", width: 48, fixed: "left" },
    { type: "globalIndex", width: 56, label: t("voice_workbench.common.index") },
    {
      prop: "original_filename",
      label: t("voice_workbench.history.originalFilename"),
      minWidth: 180,
      useSlot: true,
    },
    {
      prop: "file_size",
      label: t("voice_workbench.history.fileSize"),
      width: 100,
      formatter: (row: JobTable) => formatBytes(row.file_size),
    },
    {
      prop: "duration_sec",
      label: t("voice_workbench.history.duration"),
      width: 100,
      formatter: (row: JobTable) => formatClock(row.duration_sec),
    },
    {
      prop: "model_display_name",
      label: t("voice_workbench.history.model"),
      minWidth: 140,
      showOverflowTooltip: true,
      formatter: (row: JobTable) => row.model_display_name || row.model_name,
    },
    {
      prop: "status",
      label: t("voice_workbench.common.status"),
      width: 110,
      useSlot: true,
    },
    {
      prop: "elapsed_ms",
      label: t("voice_workbench.history.elapsed"),
      width: 100,
      formatter: (row: JobTable) => formatElapsed(row.elapsed_ms),
    },
    {
      prop: "created_time",
      label: t("voice_workbench.common.createdTime"),
      width: 190,
      showOverflowTooltip: true,
    },
    {
      prop: "operation",
      label: t("voice_workbench.common.operation"),
      width: 180,
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

watch(locale, () => {
  updateColumn?.(buildJobColumns().map((col) => ({ prop: getColumnKey(col), updates: col })));
});

async function handleSearchBarSearch(params: HistorySearchForm) {
  await searchBarRef.value?.validate?.();
  replaceSearchParams({
    original_filename: params.original_filename,
    status: params.status,
    order_by: ORDER_BY_DESC,
  });
  await getData();
}

async function onResetSearch() {
  searchForm.value = { original_filename: undefined, status: undefined };
  await resetSearchParams();
}

const detailVisible = ref(false);
const detailJob = ref<JobTable | null>(null);

const detailItems = computed<DescriptionsItem[]>(() => [
  { label: t("voice_workbench.history.originalFilename"), prop: "original_filename", span: 2 },
  { label: t("voice_workbench.history.fileSize"), prop: "file_size", slot: "file_size" },
  { label: t("voice_workbench.history.duration"), prop: "duration_sec", slot: "duration_sec" },
  { label: t("voice_workbench.history.model"), prop: "model_display_name", slot: "model_display_name" },
  { label: t("voice_workbench.common.status"), prop: "status", slot: "status" },
  { label: t("voice_workbench.history.elapsed"), prop: "elapsed_ms", slot: "elapsed_ms" },
  { label: t("voice_workbench.common.createdTime"), prop: "created_time" },
  { label: t("voice_workbench.history.startedAt"), prop: "started_at" },
  { label: t("voice_workbench.history.finishedAt"), prop: "finished_at" },
  { label: t("voice_workbench.history.error"), prop: "error", slot: "error", span: 2 },
  { label: t("voice_workbench.history.usage"), prop: "usage", slot: "usage", span: 2 },
]);

async function openDetail(row: JobTable) {
  if (row.id == null) return;
  try {
    const response = await JobAPI.detailJob(row.id);
    detailJob.value = response.data.data ?? row;
  } catch {
    detailJob.value = row;
  }
  detailVisible.value = true;
}

function handleCloseDetail() {
  detailVisible.value = false;
  detailJob.value = null;
}

function buildJobRowActions(row: JobTable): TableOperationAction[] {
  return [
    {
      key: "detail",
      label: t("voice_workbench.common.detail"),
      artType: "view",
      perm: "voice_workbench:job:query",
      run: () => {
        void openDetail(row);
      },
    },
    {
      key: "delete",
      label: t("voice_workbench.common.delete"),
      artType: "delete",
      perm: "voice_workbench:job:delete",
      run: () => {
        void deleteJob(row);
      },
    },
  ];
}

function formatJobOperationCell(row: JobTable) {
  return renderTableOperationCell(buildJobRowActions(row), {
    wrapperClass: "inline-flex flex-wrap items-center justify-end gap-1",
  });
}

async function confirmThenDelete(ids: number[], message: string) {
  try {
    await confirmAction(message, t("voice_workbench.history.deleteTitle"));
  } catch {
    return;
  }
  try {
    await JobAPI.deleteJob(ids);
    faTableRef.value?.elTableRef?.clearSelection();
    await refreshRemove();
  } catch {
    // 失败原因已由请求拦截器提示（例如任务不属于当前用户）
  }
}

async function deleteJob(row: JobTable) {
  if (row.id == null) return;
  await confirmThenDelete([row.id], t("voice_workbench.history.deleteConfirm"));
}

async function handleBatchDelete() {
  const ids = selectedIds.value;
  if (ids.length === 0) return;
  batchDeleting.value = true;
  try {
    await confirmThenDelete(
      ids,
      t("voice_workbench.history.batchDeleteConfirm", { count: ids.length })
    );
  } finally {
    batchDeleting.value = false;
  }
}

// 新转写完成后由父组件调用，回到第一页展示最新记录
defineExpose({
  refresh: refreshCreate,
});
</script>
