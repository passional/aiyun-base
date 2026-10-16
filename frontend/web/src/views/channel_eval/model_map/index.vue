<!--
  渠道评估 - 模型对齐：待确认的疑似同族（建议确认合并） / 规范模型列表（增删改查）。
  建议与未确认清单均为实时计算结果，确认后才落成规范模型。
-->
<template>
  <div class="fa-full-height">
    <!-- 待确认的疑似同族：无建议时整块不渲染 -->
    <ElCard v-if="suggestions.length > 0" class="shrink-0" shadow="never">
      <template #header>
        <div class="flex flex-wrap items-center justify-between gap-2">
          <span class="font-semibold">{{ t("channel_eval.model_map.suggestionsTitle") }}</span>
          <span class="text-xs text-(--el-text-color-secondary)">
            {{ t("channel_eval.model_map.suggestionsDesc") }}
          </span>
        </div>
      </template>

      <div class="flex max-h-96 flex-col gap-3 overflow-y-auto">
        <div
          v-for="group in suggestions"
          :key="group.normalized"
          class="rounded-md border border-(--el-border-color-lighter) p-3"
        >
          <div class="flex flex-wrap items-center justify-between gap-2">
            <div class="flex flex-wrap items-center gap-2">
              <span class="font-medium">{{ group.suggested_name }}</span>
              <span class="text-xs text-(--el-text-color-secondary)">
                {{ t("channel_eval.model_map.reasons") }}:
              </span>
              <ElTag
                v-for="reason in group.reasons"
                :key="reason"
                type="warning"
                size="small"
                effect="plain"
              >
                {{ reason }}
              </ElTag>
            </div>
            <ElButton
              v-if="canAdd"
              type="primary"
              size="small"
              :disabled="confirmSubmitting"
              @click="openConfirmDialog(group)"
            >
              {{ t("channel_eval.model_map.confirm") }}
            </ElButton>
          </div>

          <div class="mt-2 flex flex-col gap-1">
            <div
              v-for="observation in group.observations"
              :key="observation.alias"
              class="flex flex-wrap items-center gap-2"
            >
              <ElTag size="small" type="info" effect="plain">{{ observation.alias }}</ElTag>
              <span class="text-xs text-(--el-text-color-secondary)">
                {{ t("channel_eval.model_map.observedAt") }}:
                {{ formatObservationSource(observation) }}
              </span>
            </div>
          </div>
        </div>
      </div>
    </ElCard>

    <!-- 尚未确认归属的渠道模型串 -->
    <ElAlert
      v-if="unmappedCount > 0"
      class="mt-3 shrink-0"
      type="info"
      :closable="false"
      show-icon
      :title="t('channel_eval.model_map.unmappedTitle')"
    >
      <div class="text-xs">{{ t("channel_eval.model_map.unmappedDesc") }}</div>
      <div class="mt-1 flex max-h-24 flex-wrap gap-1 overflow-y-auto">
        <ElTag
          v-for="observation in unmapped.observations"
          :key="observation.alias"
          size="small"
          effect="plain"
        >
          {{ observation.alias }}
        </ElTag>
      </div>
    </ElAlert>

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
      <template #header>
        <span class="font-semibold">{{ t("channel_eval.model_map.title") }}</span>
      </template>

      <FaTableHeader
        v-model:columns="columnChecks"
        v-model:showSearchBar="showSearchBar"
        :loading="loading"
        @refresh="refreshData"
      >
        <template #left>
          <FaTableHeaderLeft
            :remove-ids="selectedIds"
            :perm-create="['channel_eval:model_map:add']"
            :perm-delete="['channel_eval:model_map:delete']"
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

    <!-- 规范模型：新增 / 编辑 -->
    <FaDialog
      v-model="dialogVisible.visible"
      :title="dialogTitle"
      width="720px"
      dialog-class="crud-embed-dialog"
      modal-class="crud-embed-dialog"
      :form-mode="dialogVisible.type"
      :confirm-loading="submitLoading"
      @cancel="handleCloseDialog"
      @close="handleCloseDialog"
      @confirm="handleSubmit()"
    >
      <FaForm
        :key="formRenderKey"
        scrollbar
        max-height="70vh"
        ref="dataFormRef"
        v-model="formData"
        :items="formItems"
        :rules="rules"
        label-suffix=":"
        :label-width="110"
        label-position="right"
        :span="24"
        :gutter="16"
        :show-reset="false"
        :show-submit="false"
        class="crud-dialog-art-form"
      >
        <template #aliases>
          <div class="w-full">
            <ElSelect
              v-model="formData.aliases"
              multiple
              filterable
              allow-create
              default-first-option
              :reserve-keyword="false"
              class="w-full"
              :placeholder="t('channel_eval.model_map.aliasesPlaceholder')"
            >
              <ElOption
                v-for="option in aliasOptions"
                :key="option.value"
                :label="option.label"
                :value="option.value"
              />
            </ElSelect>
            <div class="mt-1 text-xs text-(--el-text-color-secondary)">
              {{
                t("channel_eval.model_map.aliasCount", { count: (formData.aliases ?? []).length })
              }}
            </div>
          </div>
        </template>
      </FaForm>
    </FaDialog>

    <!-- 确认一条疑似同族建议 -->
    <FaDialog
      v-model="confirmVisible"
      :title="t('channel_eval.model_map.confirmTitle')"
      width="560px"
      dialog-class="crud-embed-dialog"
      modal-class="crud-embed-dialog"
      form-mode="create"
      :confirm-loading="confirmSubmitting"
      @cancel="closeConfirmDialog"
      @close="closeConfirmDialog"
      @confirm="handleConfirmSuggestion"
    >
      <FaForm
        :key="confirmRenderKey"
        scrollbar
        max-height="60vh"
        ref="confirmFormRef"
        v-model="confirmForm"
        :items="confirmFormItems"
        :rules="confirmRules"
        label-suffix=":"
        :label-width="96"
        label-position="right"
        :span="24"
        :gutter="16"
        :show-reset="false"
        :show-submit="false"
        class="crud-dialog-art-form"
      >
        <template #aliases>
          <div class="flex flex-wrap items-center gap-1">
            <ElTag
              v-for="alias in confirmForm.aliases"
              :key="alias"
              size="small"
              type="info"
              effect="plain"
            >
              {{ alias }}
            </ElTag>
            <span
              v-if="confirmForm.aliases.length === 0"
              class="text-xs text-(--el-text-color-secondary)"
            >
              {{ t("channel_eval.model_map.suggestionsEmpty") }}
            </span>
          </div>
        </template>
      </FaForm>
    </FaDialog>
  </div>
</template>

<script setup lang="ts">
import ModelMapAPI, {
  type AliasObservation,
  type CanonicalModelForm,
  type CanonicalModelTable,
  type ConfirmSuggestionForm,
  type SuggestionGroup,
  type UnmappedModels,
} from "@/api/module_channel_eval/model_map";
import { renderTableOperationCell, resolveStatusColumns, type TableOperationAction } from "@utils";
import { checkPerm } from "@/utils/checkPerm";
import { getColumnKey } from "@/hooks/core/useTableColumns";
import type { ColumnOption } from "@/types/component";
import type { SearchFormItem } from "@/components/forms/fa-search-bar/index.vue";
import type { FormItem } from "@/components/forms/fa-form/index.vue";
import { ElMessage, ElTag } from "element-plus";
import FaSearchBar from "@/components/forms/fa-search-bar/index.vue";
import FaForm from "@/components/forms/fa-form/index.vue";
import FaTableHeader from "@/components/tables/fa-table-header/index.vue";

defineOptions({
  name: "ChannelEvalModelMap",
  inheritAttrs: false,
});

const { t, locale } = useI18n();

const canAdd = computed(() => checkPerm("channel_eval:model_map:add"));

// ─── 通用展示辅助 ───
function orDash(value?: string | null) {
  return value || "—";
}

/** 观察来源：平台名 + 渠道名（都没有时占位） */
function formatObservationSource(observation: AliasObservation) {
  const parts = [...(observation.platforms ?? []), ...(observation.channels ?? [])];
  return parts.length > 0 ? parts.join(" / ") : "—";
}

// ─── 疑似同族建议 / 未确认清单 ───
const suggestions = ref<SuggestionGroup[]>([]);
const unmapped = ref<UnmappedModels>({ observations: [], suggestion_count: 0 });

const unmappedCount = computed(() => unmapped.value.observations.length);

async function loadSuggestions() {
  try {
    const response = await ModelMapAPI.getSuggestions();
    suggestions.value = response.data.data ?? [];
  } catch {
    // 请求失败已由拦截器提示
    suggestions.value = [];
  }
}

async function loadUnmapped() {
  try {
    const response = await ModelMapAPI.getUnmapped();
    unmapped.value = response.data.data ?? { observations: [], suggestion_count: 0 };
  } catch {
    // 请求失败已由拦截器提示
    unmapped.value = { observations: [], suggestion_count: 0 };
  }
}

onMounted(async () => {
  await Promise.all([loadSuggestions(), loadUnmapped()]);
});

// ─── 搜索 ───
type ModelMapSearchForm = {
  display_name?: string;
  family?: string;
};

const searchForm = ref<ModelMapSearchForm>({
  display_name: undefined,
  family: undefined,
});

const showSearchBar = ref(true);
const searchBarRef = ref<InstanceType<typeof FaSearchBar> | null>(null);
const searchBarRules: Record<string, unknown> = {};

const searchItems = computed<SearchFormItem[]>(() => [
  {
    label: t("channel_eval.model_map.displayName"),
    key: "display_name",
    type: "input",
    placeholder: t("channel_eval.model_map.displayNamePlaceholder"),
    clearable: true,
    span: 6,
  },
  {
    label: t("channel_eval.model_map.family"),
    key: "family",
    type: "input",
    placeholder: t("channel_eval.model_map.familyPlaceholder"),
    clearable: true,
    span: 6,
  },
]);

// ─── 表格 ───
const ORDER_BY_DESC = JSON.stringify([{ id: "desc" }]);

/** 渠道模型串列：逐个渲染为标签并自动换行 */
function renderAliasTags(row: CanonicalModelTable) {
  const aliases = row.aliases ?? [];
  if (aliases.length === 0) return "—";
  return h(
    "div",
    { class: "flex flex-wrap items-center gap-1 py-1" },
    aliases.map((item) =>
      h(
        ElTag,
        { key: item.id ?? item.alias, size: "small", type: "info", effect: "plain" },
        () => item.alias
      )
    )
  );
}

function buildCanonicalModelColumns(): ColumnOption<CanonicalModelTable>[] {
  return [
    { type: "selection", width: 48, fixed: "left" },
    { type: "globalIndex", width: 56, label: t("channel_eval.common.index") },
    {
      prop: "display_name",
      label: t("channel_eval.model_map.displayName"),
      minWidth: 160,
      showOverflowTooltip: true,
    },
    {
      prop: "family",
      label: t("channel_eval.model_map.family"),
      width: 140,
      showOverflowTooltip: true,
      formatter: (row: CanonicalModelTable) => orDash(row.family),
    },
    {
      prop: "aliases",
      label: t("channel_eval.model_map.aliases"),
      minWidth: 240,
      formatter: (row: CanonicalModelTable) => renderAliasTags(row),
    },
    {
      prop: "remark",
      label: t("channel_eval.model_map.remark"),
      minWidth: 160,
      showOverflowTooltip: true,
      formatter: (row: CanonicalModelTable) => orDash(row.remark),
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
      formatter: (row: CanonicalModelTable) => formatCanonicalModelOperationCell(row),
    },
  ];
}

const faTableRef = ref<{ elTableRef?: { clearSelection: () => void } } | null>(null);

const { selectedIds, batchDeleting, onTableSelectionChange } =
  useTableSelection<CanonicalModelTable>();

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
  refreshRemove,
  updateColumn,
} = useTable({
  core: {
    apiFn: ModelMapAPI.listCanonicalModel,
    apiParams: { page_no: 1, page_size: 10, order_by: ORDER_BY_DESC },
    columnsFactory: resolveStatusColumns<CanonicalModelTable>(() => buildCanonicalModelColumns()),
  },
});

// 语言切换后刷新列头文案（列配置工厂只在初始化时执行一次）
watch(locale, () => {
  updateColumn?.(
    buildCanonicalModelColumns().map((col) => ({ prop: getColumnKey(col), updates: col }))
  );
});

/** 渠道模型串下拉候选：库内已有别名 + 尚未确认的模型串 */
const aliasOptions = computed(() => {
  const seen = new Set<string>();
  (data.value as CanonicalModelTable[]).forEach((row) => {
    (row.aliases ?? []).forEach((item) => seen.add(item.alias));
  });
  unmapped.value.observations.forEach((item) => seen.add(item.alias));
  return Array.from(seen).map((alias) => ({ label: alias, value: alias }));
});

function buildModelMapReplaceParams(p: ModelMapSearchForm): Record<string, unknown> {
  return {
    display_name: p.display_name,
    family: p.family,
    order_by: ORDER_BY_DESC,
  };
}

async function handleSearchBarSearch(params: ModelMapSearchForm) {
  await searchBarRef.value?.validate?.();
  replaceSearchParams(buildModelMapReplaceParams(params));
  await getData();
}

async function onResetSearch() {
  searchForm.value = {
    display_name: undefined,
    family: undefined,
  };
  await resetSearchParams();
}

// ─── 对话框 / 表单 ───
const { dialogVisible } = useCrudDialog();

const dialogTitle = computed(() =>
  dialogVisible.type === "update"
    ? t("channel_eval.model_map.updateTitle")
    : t("channel_eval.model_map.createTitle")
);

const initialFormData: CanonicalModelForm = {
  id: undefined,
  display_name: "",
  family: undefined,
  remark: undefined,
  aliases: [],
};

const formData = ref<CanonicalModelForm>({ ...initialFormData });

const formRenderKey = ref(0);
const dataFormRef = ref<InstanceType<typeof FaForm> | null>(null);

const formItems = computed<FormItem[]>(() => [
  {
    label: t("channel_eval.model_map.displayName"),
    key: "display_name",
    type: "input",
    span: 24,
    props: {
      placeholder: t("channel_eval.model_map.displayNamePlaceholder"),
      maxlength: 100,
      clearable: true,
    },
  },
  {
    label: t("channel_eval.model_map.family"),
    key: "family",
    type: "input",
    span: 24,
    props: {
      placeholder: t("channel_eval.model_map.familyPlaceholder"),
      maxlength: 50,
      clearable: true,
    },
  },
  { label: t("channel_eval.model_map.aliases"), key: "aliases", span: 24 },
  {
    label: t("channel_eval.model_map.remark"),
    key: "remark",
    type: "input",
    span: 24,
    props: {
      type: "textarea",
      rows: 2,
      maxlength: 500,
      showWordLimit: true,
      placeholder: t("channel_eval.model_map.remarkPlaceholder"),
    },
  },
]);

const rules = computed(() => ({
  display_name: [
    {
      required: true,
      message: t("channel_eval.model_map.displayNamePlaceholder"),
      trigger: "blur",
    },
  ],
}));

/** 详情接口把别名对象列表摊平成表单所需的字符串列表 */
async function detailCanonicalModelForForm(id: number) {
  const response = await ModelMapAPI.detailCanonicalModel(id);
  const detail = response.data.data;
  return {
    ...response,
    data: {
      ...response.data,
      data: detail
        ? {
            ...detail,
            aliases: (detail.aliases ?? []).map((item) => item.alias),
          }
        : detail,
    },
  };
}

const { submitLoading, handleCloseDialog, handleOpenDialog, handleSubmit } =
  useCrudForm<CanonicalModelForm>({
    formData,
    initialFormData,
    dialogVisible,
    dataFormRef,
    formRenderKey,
    detailApi: detailCanonicalModelForForm,
    createApi: ModelMapAPI.createCanonicalModel,
    updateApi: ModelMapAPI.updateCanonicalModel,
    onCreateSuccess: async () => {
      ElMessage.success(t("channel_eval.model_map.saveSuccess"));
      await Promise.all([refreshCreate(), loadSuggestions(), loadUnmapped()]);
    },
    onUpdateSuccess: async () => {
      ElMessage.success(t("channel_eval.model_map.saveSuccess"));
      await Promise.all([refreshUpdate(), loadSuggestions(), loadUnmapped()]);
    },
  });

async function handleAdd() {
  createLoading.value = true;
  try {
    await handleOpenDialog("create");
  } finally {
    createLoading.value = false;
  }
}

// ─── 确认疑似同族建议 ───
const confirmVisible = ref(false);
const confirmSubmitting = ref(false);
const confirmRenderKey = ref(0);
const confirmFormRef = ref<InstanceType<typeof FaForm> | null>(null);

const confirmForm = ref<ConfirmSuggestionForm>({
  display_name: "",
  family: undefined,
  aliases: [],
});

const confirmFormItems = computed<FormItem[]>(() => [
  {
    label: t("channel_eval.model_map.suggestedName"),
    key: "display_name",
    type: "input",
    span: 24,
    props: {
      placeholder: t("channel_eval.model_map.displayNamePlaceholder"),
      maxlength: 100,
      clearable: true,
    },
  },
  {
    label: t("channel_eval.model_map.family"),
    key: "family",
    type: "input",
    span: 24,
    props: {
      placeholder: t("channel_eval.model_map.familyPlaceholder"),
      maxlength: 50,
      clearable: true,
    },
  },
  { label: t("channel_eval.model_map.aliases"), key: "aliases", span: 24 },
]);

type RuleCallback = (error?: Error) => void;

function aliasRequiredValidator(_rule: unknown, value: unknown, callback: RuleCallback) {
  const list = Array.isArray(value) ? value.filter((item) => String(item ?? "").trim()) : [];
  if (list.length === 0) {
    callback(new Error(t("channel_eval.model_map.aliasRequired")));
    return;
  }
  callback();
}

const confirmRules = computed(() => ({
  display_name: [
    {
      required: true,
      message: t("channel_eval.model_map.displayNamePlaceholder"),
      trigger: "blur",
    },
  ],
  aliases: [{ validator: aliasRequiredValidator, trigger: "change" }],
}));

function openConfirmDialog(group: SuggestionGroup) {
  confirmForm.value = {
    display_name: group.suggested_name,
    family: undefined,
    aliases: group.observations.map((item) => item.alias),
  };
  confirmRenderKey.value += 1;
  confirmVisible.value = true;
}

function closeConfirmDialog() {
  confirmVisible.value = false;
}

async function validateConfirmForm(): Promise<boolean> {
  const form = confirmFormRef.value;
  if (!form) return false;
  return await (form.validate as () => Promise<boolean>)().catch(() => false);
}

async function handleConfirmSuggestion() {
  const valid = await validateConfirmForm();
  if (!valid) return;

  confirmSubmitting.value = true;
  try {
    await ModelMapAPI.confirmSuggestion({
      display_name: confirmForm.value.display_name.trim(),
      family: confirmForm.value.family?.trim() || null,
      aliases: [...confirmForm.value.aliases],
    });
    ElMessage.success(t("channel_eval.model_map.confirmSuccess"));
    confirmVisible.value = false;
    await Promise.all([loadSuggestions(), loadUnmapped(), refreshCreate()]);
  } catch {
    // 请求失败已由拦截器提示
  } finally {
    confirmSubmitting.value = false;
  }
}

// ─── 行操作 / 删除 ───
function buildCanonicalModelRowActions(row: CanonicalModelTable): TableOperationAction[] {
  return [
    {
      key: "edit",
      label: t("channel_eval.common.edit"),
      artType: "edit",
      perm: "channel_eval:model_map:edit",
      run: () => {
        if (row.id != null) void handleOpenDialog("update", row.id);
      },
    },
    {
      key: "delete",
      label: t("channel_eval.common.delete"),
      artType: "delete",
      perm: "channel_eval:model_map:delete",
      run: () => {
        if (row.id != null) void deleteCanonicalModelRow(row);
      },
    },
  ];
}

function formatCanonicalModelOperationCell(row: CanonicalModelTable) {
  return renderTableOperationCell(buildCanonicalModelRowActions(row), {
    wrapperClass: "inline-flex flex-wrap items-center justify-end gap-1",
  });
}

async function deleteCanonicalModelRow(row: CanonicalModelTable) {
  try {
    await confirmAction(
      t("channel_eval.model_map.deleteConfirm", { name: row.display_name }),
      t("channel_eval.model_map.deleteConfirmTitle")
    );
    await ModelMapAPI.deleteCanonicalModel([row.id!]);
    faTableRef.value?.elTableRef?.clearSelection();
    await Promise.all([refreshRemove(), loadSuggestions(), loadUnmapped()]);
  } catch {
    // 用户取消或删除被拒绝（已由拦截器提示）
  }
}

async function handleBatchDelete() {
  const ids = selectedIds.value;
  if (ids.length === 0) return;
  try {
    await confirmBatchDelete(
      ids.length,
      (data.value as CanonicalModelTable[])
        .filter((row) => ids.includes(row.id!))
        .map((row) => row.display_name)
    );
    batchDeleting.value = true;
    await ModelMapAPI.deleteCanonicalModel(ids);
    faTableRef.value?.elTableRef?.clearSelection();
    await Promise.all([refreshRemove(), loadSuggestions(), loadUnmapped()]);
  } catch {
    // 用户取消或删除被拒绝（已由拦截器提示）
  } finally {
    batchDeleting.value = false;
  }
}
</script>
