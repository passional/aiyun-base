<!-- 渠道评估 - 对比报告：列表 / 编辑器 / 视图（对比图、交叉对比、异常清单、导出 HTML） -->
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
            :remove-ids="selectedIds"
            :perm-create="['channel_eval:report:add']"
            :perm-delete="['channel_eval:report:delete']"
            :delete-loading="batchDeleting"
            :create-loading="createLoading"
            @add="handleAdd"
            @delete="handleBatchDelete"
          />
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
      />
    </ElCard>

    <ReportEditor v-model="editorVisible" :report-id="editorReportId" @saved="handleSaved" />
    <ReportViewDialog v-model="viewVisible" :report-id="viewReportId" />
  </div>
</template>

<script setup lang="ts">
import ReportAPI, { type ReportTable } from "@/api/module_channel_eval/report";
import { renderTableOperationCell, resolveStatusColumns, type TableOperationAction } from "@utils";
import { getColumnKey } from "@/hooks/core/useTableColumns";
import type { ColumnOption } from "@/types/component";
import type { SearchFormItem } from "@/components/forms/fa-search-bar/index.vue";
import FaSearchBar from "@/components/forms/fa-search-bar/index.vue";
import FaTableHeader from "@/components/tables/fa-table-header/index.vue";
import ReportEditor from "./components/ReportEditor.vue";
import ReportViewDialog from "./components/ReportViewDialog.vue";

defineOptions({
  name: "ChannelEvalReport",
  inheritAttrs: false,
});

const { t, locale } = useI18n();

// ─── 搜索 ───
type ReportSearchForm = {
  name?: string;
};

const searchForm = ref<ReportSearchForm>({ name: undefined });

const showSearchBar = ref(true);
const searchBarRef = ref<InstanceType<typeof FaSearchBar> | null>(null);
const searchBarRules: Record<string, unknown> = {};

const searchItems = computed<SearchFormItem[]>(() => [
  {
    label: t("channel_eval.report.name"),
    key: "name",
    type: "input",
    placeholder: t("channel_eval.report.namePlaceholder"),
    clearable: true,
    span: 6,
  },
]);

// ─── 表格 ───
const ORDER_BY_DESC = JSON.stringify([{ id: "desc" }]);

function formatRunIds(row: ReportTable) {
  const ids = row.run_ids ?? [];
  return ids.length ? ids.map((id) => `#${id}`).join(", ") : "—";
}

function buildReportColumns(): ColumnOption<ReportTable>[] {
  return [
    { type: "selection", width: 48, fixed: "left" },
    { type: "globalIndex", width: 56, label: t("channel_eval.common.index") },
    {
      prop: "name",
      label: t("channel_eval.report.name"),
      minWidth: 160,
      showOverflowTooltip: true,
    },
    {
      prop: "run_ids",
      label: t("channel_eval.report.runIds"),
      minWidth: 140,
      showOverflowTooltip: true,
      formatter: (row) => formatRunIds(row),
    },
    {
      prop: "baseline_run_id",
      label: t("channel_eval.report.baselineRun"),
      width: 120,
      formatter: (row) => (row.baseline_run_id ? `#${row.baseline_run_id}` : "—"),
    },
    {
      prop: "note",
      label: t("channel_eval.report.note"),
      minWidth: 180,
      showOverflowTooltip: true,
      formatter: (row) => row.note || "—",
    },
    {
      prop: "created_time",
      label: t("channel_eval.common.createdTime"),
      width: 168,
      showOverflowTooltip: true,
    },
    {
      prop: "operation",
      label: t("channel_eval.common.operation"),
      width: 200,
      fixed: "right",
      align: "center",
      formatter: (row) => formatReportOperationCell(row),
    },
  ];
}

const faTableRef = ref<{ elTableRef?: { clearSelection: () => void } } | null>(null);

const { selectedIds, batchDeleting, onTableSelectionChange } = useTableSelection<ReportTable>();

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
  refreshRemove,
  updateColumn,
} = useTable({
  core: {
    apiFn: ReportAPI.listReport,
    apiParams: { page_no: 1, page_size: 10, order_by: ORDER_BY_DESC },
    columnsFactory: resolveStatusColumns<ReportTable>(() => buildReportColumns()),
  },
});

// 语言切换后刷新列头文案（列配置工厂只在初始化时执行一次）
watch(locale, () => {
  updateColumn?.(buildReportColumns().map((col) => ({ prop: getColumnKey(col), updates: col })));
});

function buildReportReplaceParams(p: ReportSearchForm): Record<string, unknown> {
  return {
    name: p.name,
    order_by: ORDER_BY_DESC,
  };
}

async function handleSearchBarSearch(params: ReportSearchForm) {
  await searchBarRef.value?.validate?.();
  replaceSearchParams(buildReportReplaceParams(params));
  await getData();
}

async function onResetSearch() {
  searchForm.value = { name: undefined };
  await resetSearchParams();
}

// ─── 编辑器 / 视图 ───
const editorVisible = ref(false);
const editorReportId = ref<number | undefined>(undefined);
const viewVisible = ref(false);
const viewReportId = ref<number | undefined>(undefined);

function handleAdd() {
  editorReportId.value = undefined;
  editorVisible.value = true;
}

async function handleSaved() {
  await refreshCreate();
}

function openView(id?: number) {
  viewReportId.value = id;
  viewVisible.value = true;
}

// ─── 行操作 / 删除 ───
function buildReportRowActions(row: ReportTable): TableOperationAction[] {
  return [
    {
      key: "view",
      label: t("channel_eval.report.viewAction"),
      artType: "view",
      perm: "channel_eval:report:query",
      run: () => {
        openView(row.id);
      },
    },
    {
      key: "edit",
      label: t("channel_eval.common.edit"),
      artType: "edit",
      perm: "channel_eval:report:edit",
      run: () => {
        if (row.id != null) {
          editorReportId.value = row.id;
          editorVisible.value = true;
        }
      },
    },
    {
      key: "delete",
      label: t("channel_eval.common.delete"),
      artType: "delete",
      perm: "channel_eval:report:delete",
      run: () => {
        if (row.id != null) void deleteReportRow(row);
      },
    },
  ];
}

function formatReportOperationCell(row: ReportTable) {
  return renderTableOperationCell(buildReportRowActions(row), {
    wrapperClass: "inline-flex flex-wrap items-center justify-end gap-1",
  });
}

async function deleteReportRow(row: ReportTable) {
  try {
    await confirmDelete(t("channel_eval.report.deleteConfirm", { name: row.name }));
    await ReportAPI.deleteReport([row.id!]);
    faTableRef.value?.elTableRef?.clearSelection();
    await refreshRemove();
  } catch {
    // 用户取消或请求失败（拦截器已提示）
  }
}

async function handleBatchDelete() {
  const ids = selectedIds.value;
  if (ids.length === 0) return;
  try {
    await confirmBatchDelete(
      ids.length,
      (data.value as ReportTable[]).filter((row) => ids.includes(row.id!)).map((row) => row.name)
    );
    batchDeleting.value = true;
    await ReportAPI.deleteReport(ids);
    faTableRef.value?.elTableRef?.clearSelection();
    await refreshRemove();
  } catch {
    // 用户取消或请求失败（拦截器已提示）
  } finally {
    batchDeleting.value = false;
  }
}
</script>
