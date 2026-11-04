<template>
  <div v-if="!serviceId" class="flex h-full items-center justify-center">
    <ElEmpty :description="t('voice_workbench.model.selectServiceHint')" />
  </div>

  <div v-else class="flex flex-col h-full overflow-hidden pl-2">
    <div class="mb-3 flex flex-wrap items-center gap-2">
      <span class="text-sm text-[var(--el-text-color-secondary)]">
        {{ t("voice_workbench.model.belongService") }}：
      </span>
      <FaStatusTag type="primary" :label="serviceName || '—'" />
    </div>

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
            :perm-create="['voice_workbench:service:add']"
            :perm-delete="['voice_workbench:service:delete']"
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

    <FaDialog
      v-model="dialogVisible.visible"
      :title="dialogVisible.title"
      width="820px"
      dialog-class="crud-embed-dialog"
      modal-class="crud-embed-dialog"
      :form-mode="dialogVisible.type"
      :confirm-loading="submitLoading"
      @cancel="handleCloseDialog"
      @close="handleCloseDialog"
      @confirm="handleSubmit()"
    >
      <template v-if="dialogVisible.type === 'detail'">
        <FaDescriptions
          :column="2"
          :data="detailFormData"
          :items="detailItems"
          label-width="130px"
          max-height="70vh"
        >
          <template #status="{ row }">
            <FaStatusTag
              :type="asModel(row)?.status === 0 ? 'success' : 'danger'"
              :label="statusLabel(asModel(row)?.status)"
            />
          </template>
        </FaDescriptions>
      </template>
      <template v-else>
        <FaForm
          :key="formRenderKey"
          scrollbar
          max-height="70vh"
          ref="dataFormRef"
          v-model="formData"
          :items="formItems"
          :rules="rules"
          label-suffix=":"
          :label-width="130"
          label-position="right"
          :span="24"
          :gutter="16"
          :show-reset="false"
          :show-submit="false"
          class="crud-dialog-art-form"
        >
          <template #status>
            <ElRadioGroup v-model="formData.status">
              <ElRadio :value="0">{{ t("voice_workbench.common.enable") }}</ElRadio>
              <ElRadio :value="1">{{ t("voice_workbench.common.disable") }}</ElRadio>
            </ElRadioGroup>
          </template>
        </FaForm>
      </template>
    </FaDialog>
  </div>
</template>

<script setup lang="ts">
import ModelAPI, {
  type ModelForm,
  type ModelPageQuery,
  type ModelTable,
} from "@/api/module_voice_workbench/model";
import { renderTableOperationCell, resolveStatusColumns, type TableOperationAction } from "@utils";
import { getColumnKey } from "@/hooks/core/useTableColumns";
import type { ColumnOption } from "@/types/component";
import type { SearchFormItem } from "@/components/forms/fa-search-bar/index.vue";
import type { FormItem } from "@/components/forms/fa-form/index.vue";
import type { DescriptionsItem } from "@/components/display/fa-descriptions/index.vue";
import FaSearchBar from "@/components/forms/fa-search-bar/index.vue";
import FaForm from "@/components/forms/fa-form/index.vue";
import FaDescriptions from "@/components/display/fa-descriptions/index.vue";
import FaTableHeader from "@/components/tables/fa-table-header/index.vue";
import FaTableHeaderLeft from "@/components/tables/fa-table-header-left/index.vue";

defineOptions({
  name: "VoiceWorkbenchModelPanel",
  inheritAttrs: false,
});

const props = defineProps<{
  serviceId: number;
  serviceName: string;
}>();

const { t, locale } = useI18n();

function statusLabel(value?: number) {
  return value === 1 ? t("voice_workbench.common.disable") : t("voice_workbench.common.enable");
}

/** 详情插槽的 row 由 FaDescriptions 以宽松类型下发，这里收回具体的列表项类型 */
function asModel(row: unknown): ModelTable | null {
  return (row as ModelTable | null) ?? null;
}

type ModelSearchForm = {
  display_name?: string;
  status?: number;
};

const searchForm = ref<ModelSearchForm>({ display_name: undefined, status: undefined });
const showSearchBar = ref(true);
const searchBarRef = ref<InstanceType<typeof FaSearchBar> | null>(null);
const searchBarRules: Record<string, unknown> = {};

const statusOptions = computed(() => [
  { label: t("voice_workbench.common.enable"), value: 0 },
  { label: t("voice_workbench.common.disable"), value: 1 },
]);

const searchItems = computed<SearchFormItem[]>(() => [
  {
    label: t("voice_workbench.model.displayName"),
    key: "display_name",
    type: "input",
    placeholder: t("voice_workbench.model.displayNamePlaceholder"),
    clearable: true,
    span: 8,
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
    span: 8,
  },
]);

const ORDER_BY_ASC = JSON.stringify([{ id: "asc" }]);

function buildModelColumns(): ColumnOption<ModelTable>[] {
  return [
    { type: "selection", width: 48, fixed: "left" },
    { type: "globalIndex", width: 56, label: t("voice_workbench.common.index") },
    {
      prop: "display_name",
      label: t("voice_workbench.model.displayName"),
      minWidth: 140,
      showOverflowTooltip: true,
    },
    {
      prop: "model_name",
      label: t("voice_workbench.model.modelName"),
      minWidth: 130,
      showOverflowTooltip: true,
    },
    {
      prop: "resource_id",
      label: t("voice_workbench.model.resourceId"),
      minWidth: 180,
      showOverflowTooltip: true,
    },
    {
      prop: "endpoint_url",
      label: t("voice_workbench.model.endpointUrl"),
      minWidth: 220,
      showOverflowTooltip: true,
    },
    {
      prop: "max_file_mb",
      label: t("voice_workbench.model.maxFileMb"),
      width: 110,
    },
    {
      prop: "max_duration_sec",
      label: t("voice_workbench.model.maxDurationSec"),
      width: 120,
    },
    {
      prop: "status",
      label: t("voice_workbench.common.status"),
      width: 90,
      status: {
        0: { type: "success", text: t("voice_workbench.common.enable") },
        1: { type: "danger", text: t("voice_workbench.common.disable") },
      },
    },
    {
      prop: "remark",
      label: t("voice_workbench.model.remark"),
      minWidth: 120,
      showOverflowTooltip: true,
    },
    {
      prop: "operation",
      label: t("voice_workbench.common.operation"),
      width: 200,
      fixed: "right",
      align: "center",
      formatter: (row: ModelTable) => formatModelOperationCell(row),
    },
  ];
}

const faTableRef = ref<{ elTableRef?: { clearSelection: () => void } } | null>(null);
const { selectedIds, batchDeleting, onTableSelectionChange } = useTableSelection<ModelTable>();
const createLoading = ref(false);

/** 列表接口按 service_id 过滤（两级结构：模型永远属于某个语音服务） */
async function fetchModelListMerged(params: Record<string, unknown>) {
  const query: ModelPageQuery = {
    page_no: Number(params.page_no) || 1,
    page_size: Number(params.page_size) || 10,
    service_id: props.serviceId,
    display_name: (params.display_name as string) || undefined,
    status:
      params.status !== undefined && params.status !== null ? Number(params.status) : undefined,
    order_by: ORDER_BY_ASC,
  };
  return ModelAPI.listModel(query);
}

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
    apiFn: fetchModelListMerged,
    // 父页尚未选中语音服务时传入 0；面板仍会挂载，但此时不能请求列表。
    immediate: props.serviceId > 0,
    apiParams: { page_no: 1, page_size: 10, service_id: props.serviceId, order_by: ORDER_BY_ASC },
    columnsFactory: resolveStatusColumns<ModelTable>(() => buildModelColumns()),
  },
});

watch(locale, () => {
  updateColumn?.(buildModelColumns().map((col) => ({ prop: getColumnKey(col), updates: col })));
});

async function handleSearchBarSearch(params: ModelSearchForm) {
  await searchBarRef.value?.validate?.();
  replaceSearchParams({
    display_name: params.display_name,
    status: params.status,
    order_by: ORDER_BY_ASC,
  });
  await getData();
}

async function onResetSearch() {
  searchForm.value = { display_name: undefined, status: undefined };
  await resetSearchParams();
}

const { dialogVisible } = useCrudDialog();

const detailFormData = ref<Partial<ModelTable>>({});

function createInitialFormData(): ModelForm {
  return {
    id: undefined,
    service_id: props.serviceId,
    display_name: "",
    model_name: "",
    resource_id: "",
    endpoint_url: "",
    max_file_mb: 20,
    max_duration_sec: 300,
    status: 0,
    remark: undefined,
  };
}

const initialFormData = createInitialFormData();
const formData = ref<ModelForm>(createInitialFormData());
const formRenderKey = ref(0);
const dataFormRef = ref<InstanceType<typeof FaForm> | null>(null);

const detailItems = computed<DescriptionsItem[]>(() => [
  { label: t("voice_workbench.model.displayName"), prop: "display_name" },
  { label: t("voice_workbench.model.modelName"), prop: "model_name" },
  { label: t("voice_workbench.model.resourceId"), prop: "resource_id" },
  { label: t("voice_workbench.model.endpointUrl"), prop: "endpoint_url", span: 2 },
  { label: t("voice_workbench.model.maxFileMb"), prop: "max_file_mb" },
  { label: t("voice_workbench.model.maxDurationSec"), prop: "max_duration_sec" },
  { label: t("voice_workbench.common.status"), prop: "status", slot: "status" },
  { label: t("voice_workbench.model.remark"), prop: "remark", span: 2 },
  { label: t("voice_workbench.common.createdTime"), prop: "created_time" },
  { label: t("voice_workbench.common.updatedTime"), prop: "updated_time" },
]);

const formItems = computed<FormItem[]>(() => [
  {
    label: t("voice_workbench.model.displayName"),
    key: "display_name",
    type: "input",
    span: 12,
    props: {
      placeholder: t("voice_workbench.model.displayNamePlaceholder"),
      maxlength: 100,
      clearable: true,
    },
  },
  {
    label: t("voice_workbench.model.modelName"),
    key: "model_name",
    type: "input",
    span: 12,
    props: {
      placeholder: t("voice_workbench.model.modelNamePlaceholder"),
      maxlength: 128,
      clearable: true,
    },
  },
  {
    label: t("voice_workbench.model.resourceId"),
    key: "resource_id",
    type: "input",
    span: 12,
    props: {
      placeholder: t("voice_workbench.model.resourceIdPlaceholder"),
      maxlength: 128,
      clearable: true,
    },
  },
  {
    label: t("voice_workbench.model.endpointUrl"),
    key: "endpoint_url",
    type: "input",
    span: 12,
    props: {
      placeholder: t("voice_workbench.model.endpointUrlPlaceholder"),
      maxlength: 512,
      clearable: true,
    },
  },
  {
    label: t("voice_workbench.model.maxFileMb"),
    key: "max_file_mb",
    type: "number",
    span: 12,
    props: {
      min: 1,
      max: 500,
      step: 1,
      controlsPosition: "right",
    },
  },
  {
    label: t("voice_workbench.model.maxDurationSec"),
    key: "max_duration_sec",
    type: "number",
    span: 12,
    props: {
      min: 1,
      max: 1800,
      step: 1,
      controlsPosition: "right",
    },
  },
  { label: t("voice_workbench.common.status"), key: "status", type: "radiogroup", span: 24 },
  {
    label: t("voice_workbench.model.remark"),
    key: "remark",
    type: "input",
    span: 24,
    props: {
      type: "textarea",
      rows: 2,
      maxlength: 500,
      showWordLimit: true,
      placeholder: t("voice_workbench.model.remarkPlaceholder"),
    },
  },
]);

type RuleCallback = (error?: Error) => void;

/** 端点必须是完整的 WebSocket URL（后端按它连接上游） */
function endpointValidator(_rule: unknown, value: unknown, callback: RuleCallback) {
  const text = String(value ?? "").trim();
  if (!text) {
    callback();
    return;
  }
  if (!/^wss?:\/\//i.test(text)) {
    callback(new Error(t("voice_workbench.model.endpointUrlInvalid")));
    return;
  }
  try {
    new URL(text);
  } catch {
    callback(new Error(t("voice_workbench.model.endpointUrlInvalid")));
    return;
  }
  callback();
}

const rules = computed(() => ({
  display_name: [
    { required: true, message: t("voice_workbench.model.displayNameRequired"), trigger: "blur" },
  ],
  model_name: [
    { required: true, message: t("voice_workbench.model.modelNameRequired"), trigger: "blur" },
  ],
  resource_id: [
    { required: true, message: t("voice_workbench.model.resourceIdRequired"), trigger: "blur" },
  ],
  endpoint_url: [
    { required: true, message: t("voice_workbench.model.endpointUrlRequired"), trigger: "blur" },
    { validator: endpointValidator, trigger: "blur" },
  ],
  max_file_mb: [
    {
      type: "number",
      required: true,
      min: 1,
      max: 500,
      message: t("voice_workbench.model.maxFileMbRange"),
      trigger: "change",
    },
  ],
  max_duration_sec: [
    {
      type: "number",
      required: true,
      min: 1,
      max: 1800,
      message: t("voice_workbench.model.maxDurationSecRange"),
      trigger: "change",
    },
  ],
}));

/** 只提交表单字段：详情出参里的 service_name 等只读字段不回流到写接口 */
function toModelPayload(form: ModelForm): ModelForm {
  return {
    service_id: form.service_id ?? props.serviceId,
    display_name: form.display_name,
    model_name: form.model_name,
    resource_id: form.resource_id,
    endpoint_url: form.endpoint_url,
    max_file_mb: form.max_file_mb,
    max_duration_sec: form.max_duration_sec,
    status: form.status,
    remark: form.remark,
  };
}

const { submitLoading, handleCloseDialog, handleOpenDialog, handleSubmit } =
  useCrudForm<ModelForm>({
    formData,
    initialFormData,
    dialogVisible,
    dataFormRef,
    formRenderKey,
    detailApi: ModelAPI.detailModel,
    createApi: (form) => ModelAPI.createModel(toModelPayload(form)),
    updateApi: (id, form) => ModelAPI.updateModel(id, toModelPayload(form)),
    detailFormData,
    titles: {
      create: t("voice_workbench.model.createTitle"),
      update: t("voice_workbench.model.updateTitle"),
      detail: t("voice_workbench.model.detailTitle"),
    },
    onCreateSuccess: async () => {
      await refreshCreate();
    },
    onUpdateSuccess: async () => {
      await refreshUpdate();
    },
  });

async function handleAdd() {
  createLoading.value = true;
  try {
    await handleOpenDialog("create");
    formData.value.service_id = props.serviceId;
  } finally {
    createLoading.value = false;
  }
}

function buildModelRowActions(row: ModelTable): TableOperationAction[] {
  return [
    {
      key: "detail",
      label: t("voice_workbench.common.detail"),
      artType: "view",
      perm: "voice_workbench:service:query",
      run: () => {
        if (row.id != null) void handleOpenDialog("detail", row.id);
      },
    },
    {
      key: "edit",
      label: t("voice_workbench.common.edit"),
      artType: "edit",
      perm: "voice_workbench:service:edit",
      run: () => {
        if (row.id != null) void handleOpenDialog("update", row.id);
      },
    },
    {
      key: "delete",
      label: t("voice_workbench.common.delete"),
      artType: "delete",
      perm: "voice_workbench:service:delete",
      run: () => {
        void deleteModelRow(row);
      },
    },
  ];
}

function formatModelOperationCell(row: ModelTable) {
  return renderTableOperationCell(buildModelRowActions(row), {
    wrapperClass: "inline-flex flex-wrap items-center justify-end gap-1",
  });
}

async function deleteModelRow(row: ModelTable) {
  try {
    await confirmDelete(t("voice_workbench.model.deleteConfirm", { name: row.display_name }));
    await ModelAPI.deleteModel([row.id!]);
    faTableRef.value?.elTableRef?.clearSelection();
    await refreshRemove();
  } catch {
    // 用户取消，或被引用保护拒绝（已由拦截器提示改为停用）
  }
}

async function handleBatchDelete() {
  const ids = selectedIds.value;
  if (ids.length === 0) return;
  try {
    await confirmBatchDelete(
      ids.length,
      (data.value as ModelTable[])
        .filter((row) => ids.includes(row.id!))
        .map((row) => row.display_name)
    );
    batchDeleting.value = true;
    await ModelAPI.deleteModel(ids);
    faTableRef.value?.elTableRef?.clearSelection();
    await refreshRemove();
  } catch {
    // 用户取消，或被引用保护拒绝（已由拦截器提示改为停用）
  } finally {
    batchDeleting.value = false;
  }
}
</script>
