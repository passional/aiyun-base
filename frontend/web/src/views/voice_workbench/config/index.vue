<template>
  <div class="fa-full-height">
    <ElSplitter :style="'height: 100%'">
      <ElSplitterPanel size="34%" :min="360">
        <div class="flex flex-col h-full overflow-hidden pr-2">
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
              highlight-current-row
              @row-click="handleServiceRowClick"
              @selection-change="onTableSelectionChange"
              @pagination:size-change="handleSizeChange"
              @pagination:current-change="handleCurrentChange"
            />
          </ElCard>
        </div>
      </ElSplitterPanel>

      <ElSplitterPanel :min="320">
        <div class="flex flex-col h-full overflow-hidden pl-2">
          <ModelPanel
            :key="currentServiceId"
            :service-id="currentServiceId"
            :service-name="currentServiceName"
          />
        </div>
      </ElSplitterPanel>
    </ElSplitter>

    <FaDialog
      v-model="dialogVisible.visible"
      :title="dialogVisible.title"
      width="720px"
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
          label-width="120px"
          max-height="70vh"
        >
          <template #status="{ row }">
            <FaStatusTag
              :type="asService(row)?.status === 0 ? 'success' : 'danger'"
              :label="statusLabel(asService(row)?.status)"
            />
          </template>
          <template #credential="{ row }">
            <div class="flex flex-wrap items-center gap-2">
              <span>{{ credentialLabel(asService(row)) }}</span>
              <FaStatusTag
                :type="asService(row)?.has_api_key ? 'success' : 'info'"
                :label="
                  asService(row)?.has_api_key
                    ? t('voice_workbench.service.configured')
                    : t('voice_workbench.service.notConfigured')
                "
              />
            </div>
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
          :label-width="110"
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
import ServiceAPI, {
  type ServiceForm,
  type ServiceTable,
} from "@/api/module_voice_workbench/service";
import ModelPanel from "./components/VoiceModelPanel.vue";
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

// 与菜单 route_name 一致：KeepAlive 的 include / exclude 按组件 name 匹配
defineOptions({
  name: "VoiceWorkbenchConfig",
  inheritAttrs: false,
});

const { t, locale } = useI18n();

function statusLabel(value?: number) {
  return value === 1 ? t("voice_workbench.common.disable") : t("voice_workbench.common.enable");
}

function credentialLabel(row?: ServiceTable | null) {
  if (!row) return "—";
  return row.api_key_masked || (row.has_api_key ? "••••" : t("voice_workbench.service.notConfigured"));
}

/** 详情插槽的 row 由 FaDescriptions 以宽松类型下发，这里收回具体的列表项类型 */
function asService(row: unknown): ServiceTable | null {
  return (row as ServiceTable | null) ?? null;
}

type ServiceSearchForm = {
  name?: string;
  status?: number;
};

const searchForm = ref<ServiceSearchForm>({ name: undefined, status: undefined });
const showSearchBar = ref(true);
const searchBarRef = ref<InstanceType<typeof FaSearchBar> | null>(null);
const searchBarRules: Record<string, unknown> = {};

const statusOptions = computed(() => [
  { label: t("voice_workbench.common.enable"), value: 0 },
  { label: t("voice_workbench.common.disable"), value: 1 },
]);

const searchItems = computed<SearchFormItem[]>(() => [
  {
    label: t("voice_workbench.service.name"),
    key: "name",
    type: "input",
    placeholder: t("voice_workbench.service.namePlaceholder"),
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

function buildServiceColumns(): ColumnOption<ServiceTable>[] {
  return [
    { type: "selection", width: 48, fixed: "left" },
    { type: "globalIndex", width: 56, label: t("voice_workbench.common.index") },
    {
      prop: "name",
      label: t("voice_workbench.service.name"),
      minWidth: 150,
      showOverflowTooltip: true,
    },
    {
      prop: "api_key_masked",
      label: t("voice_workbench.service.apiKey"),
      minWidth: 130,
      formatter: (row: ServiceTable) => credentialLabel(row),
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
      label: t("voice_workbench.service.remark"),
      minWidth: 120,
      showOverflowTooltip: true,
    },
    {
      prop: "operation",
      label: t("voice_workbench.common.operation"),
      width: 200,
      fixed: "right",
      align: "center",
      formatter: (row: ServiceTable) => formatServiceOperationCell(row),
    },
  ];
}

const faTableRef = ref<{
  elTableRef?: { clearSelection: () => void; setCurrentRow: (row: ServiceTable) => void };
} | null>(null);

const { selectedIds, batchDeleting, onTableSelectionChange } = useTableSelection<ServiceTable>();
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
    apiFn: ServiceAPI.listService,
    apiParams: { page_no: 1, page_size: 10, order_by: ORDER_BY_ASC },
    columnsFactory: resolveStatusColumns<ServiceTable>(() => buildServiceColumns()),
  },
});

watch(locale, () => {
  updateColumn?.(buildServiceColumns().map((col) => ({ prop: getColumnKey(col), updates: col })));
});

async function handleSearchBarSearch(params: ServiceSearchForm) {
  await searchBarRef.value?.validate?.();
  replaceSearchParams({
    name: params.name,
    status: params.status,
    order_by: ORDER_BY_ASC,
  });
  await getData();
}

async function onResetSearch() {
  searchForm.value = { name: undefined, status: undefined };
  await resetSearchParams();
}

const currentServiceId = ref(0);
const currentServiceName = ref("");
const hasAutoSelected = ref(false);

function handleServiceRowClick(row: ServiceTable) {
  currentServiceId.value = row.id ?? 0;
  currentServiceName.value = row.name || "";
}

watch(data, (rows) => {
  const list = rows as ServiceTable[];
  if (list.length > 0 && !hasAutoSelected.value) {
    handleServiceRowClick(list[0]!);
    hasAutoSelected.value = true;
  }
  if (list.length === 0) {
    currentServiceId.value = 0;
    currentServiceName.value = "";
  }
});

const { dialogVisible } = useCrudDialog();

const detailFormData = ref<Partial<ServiceTable>>({});

const detailItems = computed<DescriptionsItem[]>(() => [
  { label: t("voice_workbench.service.name"), prop: "name" },
  { label: t("voice_workbench.common.status"), prop: "status", slot: "status" },
  { label: t("voice_workbench.service.apiKey"), prop: "api_key_masked", slot: "credential" },
  { label: t("voice_workbench.service.remark"), prop: "remark", span: 2 },
  { label: t("voice_workbench.common.createdTime"), prop: "created_time" },
  { label: t("voice_workbench.common.updatedTime"), prop: "updated_time" },
]);

const initialFormData: ServiceForm = {
  id: undefined,
  name: "",
  api_key: undefined,
  status: 0,
  remark: undefined,
};

const formData = ref<ServiceForm>({ ...initialFormData });
const formRenderKey = ref(0);
const dataFormRef = ref<InstanceType<typeof FaForm> | null>(null);

const formItems = computed<FormItem[]>(() => {
  const isEdit = dialogVisible.type === "update";
  return [
    {
      label: t("voice_workbench.service.name"),
      key: "name",
      type: "input",
      span: 24,
      props: {
        placeholder: t("voice_workbench.service.namePlaceholder"),
        maxlength: 100,
        clearable: true,
      },
    },
    {
      label: t("voice_workbench.service.apiKey"),
      key: "api_key",
      type: "input",
      span: 24,
      props: {
        type: "password",
        showPassword: true,
        autocomplete: "new-password",
        clearable: true,
        maxlength: 512,
        placeholder: isEdit
          ? t("voice_workbench.service.apiKeyEditPlaceholder")
          : t("voice_workbench.service.apiKeyCreatePlaceholder"),
      },
    },
    { label: t("voice_workbench.common.status"), key: "status", type: "radiogroup", span: 24 },
    {
      label: t("voice_workbench.service.remark"),
      key: "remark",
      type: "input",
      span: 24,
      props: {
        type: "textarea",
        rows: 2,
        maxlength: 500,
        showWordLimit: true,
        placeholder: t("voice_workbench.service.remarkPlaceholder"),
      },
    },
  ];
});

const rules = computed(() => ({
  name: [
    { required: true, message: t("voice_workbench.service.nameRequired"), trigger: "blur" },
  ],
}));

/** 只提交表单字段：详情出参里的脱敏凭证等只读字段不回流到写接口 */
function toServicePayload(form: ServiceForm): ServiceForm {
  return {
    name: form.name,
    api_key: form.api_key,
    status: form.status,
    remark: form.remark,
  };
}

const { submitLoading, handleCloseDialog, handleOpenDialog, handleSubmit } =
  useCrudForm<ServiceForm>({
    formData,
    initialFormData,
    dialogVisible,
    dataFormRef,
    formRenderKey,
    detailApi: ServiceAPI.detailService,
    createApi: (form) => ServiceAPI.createService(toServicePayload(form)),
    updateApi: (id, form) => ServiceAPI.updateService(id, toServicePayload(form)),
    detailFormData,
    titles: {
      create: t("voice_workbench.service.createTitle"),
      update: t("voice_workbench.service.updateTitle"),
      detail: t("voice_workbench.service.detailTitle"),
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
  } finally {
    createLoading.value = false;
  }
}

function buildServiceRowActions(row: ServiceTable): TableOperationAction[] {
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
        void deleteServiceRow(row);
      },
    },
  ];
}

function formatServiceOperationCell(row: ServiceTable) {
  return renderTableOperationCell(buildServiceRowActions(row), {
    wrapperClass: "inline-flex flex-wrap items-center justify-end gap-1",
  });
}

async function deleteServiceRow(row: ServiceTable) {
  try {
    await confirmDelete(t("voice_workbench.service.deleteConfirm", { name: row.name }));
    await ServiceAPI.deleteService([row.id!]);
    faTableRef.value?.elTableRef?.clearSelection();
    if (currentServiceId.value === row.id) {
      currentServiceId.value = 0;
      currentServiceName.value = "";
      hasAutoSelected.value = false;
    }
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
      (data.value as ServiceTable[]).filter((row) => ids.includes(row.id!)).map((row) => row.name)
    );
    batchDeleting.value = true;
    await ServiceAPI.deleteService(ids);
    faTableRef.value?.elTableRef?.clearSelection();
    if (ids.includes(currentServiceId.value)) {
      currentServiceId.value = 0;
      currentServiceName.value = "";
      hasAutoSelected.value = false;
    }
    await refreshRemove();
  } catch {
    // 用户取消，或被引用保护拒绝（已由拦截器提示改为停用）
  } finally {
    batchDeleting.value = false;
  }
}
</script>
