<!-- 绘图工作台 - 绘图服务配置：绘图服务列表（左）+ 其下绘图模型的编辑（右） -->
<template>
  <div class="fa-full-height">
    <ElSplitter :style="'height: 100%'">
      <!-- 左：绘图服务 -->
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
                  :perm-create="['image_workbench:service:add']"
                  :perm-delete="['image_workbench:service:delete']"
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

      <!-- 右：所选绘图服务下的绘图模型 -->
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

    <!-- 绘图服务 CRUD -->
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
          <template #dialect="{ row }">
            <FaStatusTag
              type="primary"
              :label="asService(row)?.dialect_label || dialectLabel(asService(row)?.dialect)"
            />
          </template>
          <template #status="{ row }">
            <FaStatusTag
              :type="asService(row)?.status === 0 ? 'success' : 'danger'"
              :label="statusLabel(asService(row)?.status)"
            />
          </template>
          <template #api_key_masked="{ row }">
            {{ orDash(asService(row)?.api_key_masked) }}
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
          <template #dialect>
            <ElRadioGroup v-model="formData.dialect">
              <ElRadio value="ark">{{ $t("image_workbench.common.dialectArk") }}</ElRadio>
              <ElRadio value="openai">{{ $t("image_workbench.common.dialectOpenai") }}</ElRadio>
            </ElRadioGroup>
          </template>
          <template #base_url_tip>
            <ElAlert type="info" :closable="false" show-icon>
              {{ $t("image_workbench.service.baseUrlTip") }}
            </ElAlert>
          </template>
          <template #status>
            <ElRadioGroup v-model="formData.status">
              <ElRadio :value="0">{{ $t("image_workbench.common.enable") }}</ElRadio>
              <ElRadio :value="1">{{ $t("image_workbench.common.disable") }}</ElRadio>
            </ElRadioGroup>
          </template>
          <template #test_connection>
            <div class="flex flex-wrap items-center gap-2">
              <ElButton
                :loading="testLoading"
                :disabled="testLoading"
                @click="handleTestConnection"
              >
                {{ $t("image_workbench.common.testConnection") }}
              </ElButton>
              <ElTag
                v-if="testResult"
                :type="testResult.success ? 'success' : 'danger'"
                class="max-w-full whitespace-normal! h-auto! py-1"
              >
                {{ testResult.message }}
              </ElTag>
            </div>
          </template>
        </FaForm>
      </template>
    </FaDialog>
  </div>
</template>

<script setup lang="ts">
import { useCrudForm } from "@/hooks/core/useCrudForm";
import ServiceAPI, {
  type ServiceDialect,
  type ServiceForm,
  type ServiceTable,
  type TestConnectionPayload,
  type TestConnectionResult,
} from "@/api/module_image_workbench/service";
import ModelPanel from "./components/ModelPanel.vue";
import { renderTableOperationCell, resolveStatusColumns, type TableOperationAction } from "@utils";
import { getColumnKey } from "@/hooks/core/useTableColumns";
import type { ColumnOption } from "@/types/component";
import type { SearchFormItem } from "@/components/forms/fa-search-bar/index.vue";
import type { FormItem } from "@/components/forms/fa-form/index.vue";
import type { DescriptionsItem } from "@/components/display/fa-descriptions/index.vue";
import { ElMessage } from "element-plus";
import FaSearchBar from "@/components/forms/fa-search-bar/index.vue";
import FaForm from "@/components/forms/fa-form/index.vue";
import FaDescriptions from "@/components/display/fa-descriptions/index.vue";
import FaTableHeader from "@/components/tables/fa-table-header/index.vue";

// 与菜单 route_name 一致：KeepAlive 的 include / exclude 按组件 name 匹配
defineOptions({
  name: "ImageWorkbenchConfig",
  inheritAttrs: false,
});

const { t, locale } = useI18n();

// ── 通用展示辅助 ───
function orDash(value?: string | null) {
  return value || "—";
}

function dialectLabel(value?: ServiceDialect) {
  return value === "openai"
    ? t("image_workbench.common.dialectOpenai")
    : t("image_workbench.common.dialectArk");
}

function statusLabel(value?: number) {
  return value === 1 ? t("image_workbench.common.disable") : t("image_workbench.common.enable");
}

/** 详情插槽的 row 由 FaDescriptions 以宽松类型下发，这里收回具体的列表项类型 */
function asService(row: unknown): ServiceTable | null {
  return (row as ServiceTable | null) ?? null;
}

// ── 搜索 ───
type ServiceSearchForm = {
  name?: string;
  dialect?: ServiceDialect;
  status?: number;
};

const searchForm = ref<ServiceSearchForm>({
  name: undefined,
  dialect: undefined,
  status: undefined,
});
const showSearchBar = ref(true);
const searchBarRef = ref<InstanceType<typeof FaSearchBar> | null>(null);
const searchBarRules: Record<string, unknown> = {};

const dialectOptions = computed(() => [
  { label: t("image_workbench.common.dialectArk"), value: "ark" as ServiceDialect },
  { label: t("image_workbench.common.dialectOpenai"), value: "openai" as ServiceDialect },
]);

const statusOptions = computed(() => [
  { label: t("image_workbench.common.enable"), value: 0 },
  { label: t("image_workbench.common.disable"), value: 1 },
]);

const searchItems = computed<SearchFormItem[]>(() => [
  {
    label: t("image_workbench.service.name"),
    key: "name",
    type: "input",
    placeholder: t("image_workbench.service.namePlaceholder"),
    clearable: true,
    span: 8,
  },
  {
    label: t("image_workbench.service.dialect"),
    key: "dialect",
    type: "select",
    props: {
      placeholder: t("image_workbench.common.selectPlaceholder"),
      options: dialectOptions.value,
      clearable: true,
    },
    span: 8,
  },
  {
    label: t("image_workbench.common.status"),
    key: "status",
    type: "select",
    props: {
      placeholder: t("image_workbench.common.selectPlaceholder"),
      options: statusOptions.value,
      clearable: true,
    },
    span: 8,
  },
]);

// ─── 表格 ───
const ORDER_BY_ASC = JSON.stringify([{ id: "asc" }]);

function buildServiceColumns(): ColumnOption<ServiceTable>[] {
  return [
    { type: "selection", width: 48, fixed: "left" },
    { type: "globalIndex", width: 56, label: t("image_workbench.common.index") },
    {
      prop: "name",
      label: t("image_workbench.service.name"),
      minWidth: 150,
      showOverflowTooltip: true,
    },
    {
      prop: "dialect",
      label: t("image_workbench.service.dialect"),
      width: 120,
      formatter: (row: ServiceTable) => row.dialect_label || dialectLabel(row.dialect),
    },
    {
      prop: "base_url",
      label: t("image_workbench.service.baseUrl"),
      minWidth: 220,
      showOverflowTooltip: true,
    },
    {
      prop: "api_key_masked",
      label: t("image_workbench.service.apiKey"),
      minWidth: 120,
      formatter: (row: ServiceTable) => orDash(row.api_key_masked),
    },
    {
      prop: "status",
      label: t("image_workbench.common.status"),
      width: 90,
      status: {
        0: { type: "success", text: t("image_workbench.common.enable") },
        1: { type: "danger", text: t("image_workbench.common.disable") },
      },
    },
    {
      prop: "remark",
      label: t("image_workbench.service.remark"),
      minWidth: 120,
      showOverflowTooltip: true,
    },
    {
      prop: "operation",
      label: t("image_workbench.common.operation"),
      width: 220,
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

// 语言切换后刷新列头文案（列配置工厂只在初始化时执行一次）
watch(locale, () => {
  updateColumn?.(buildServiceColumns().map((col) => ({ prop: getColumnKey(col), updates: col })));
});

async function handleSearchBarSearch(params: ServiceSearchForm) {
  await searchBarRef.value?.validate?.();
  replaceSearchParams({
    name: params.name,
    dialect: params.dialect,
    status: params.status,
    order_by: ORDER_BY_ASC,
  });
  await getData();
}

async function onResetSearch() {
  searchForm.value = { name: undefined, dialect: undefined, status: undefined };
  await resetSearchParams();
}

// ── 主从选择：选中一行 → 右侧展示其下绘图模型 ───
const currentServiceId = ref(0);
const currentServiceName = ref("");
const hasAutoSelected = ref(false);

function handleServiceRowClick(row: ServiceTable) {
  currentServiceId.value = row.id ?? 0;
  currentServiceName.value = row.name || "";
}

// 首次加载后自动选中第一行；列表清空时收起右侧面板
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

// ── 对话框 / 表单 ──
const { dialogVisible } = useCrudDialog();

const detailFormData = ref<Partial<ServiceTable>>({});

const detailItems = computed<DescriptionsItem[]>(() => [
  { label: t("image_workbench.service.name"), prop: "name" },
  { label: t("image_workbench.service.dialect"), prop: "dialect", slot: "dialect" },
  { label: t("image_workbench.service.baseUrl"), prop: "base_url", span: 2 },
  { label: t("image_workbench.common.status"), prop: "status", slot: "status" },
  { label: t("image_workbench.service.apiKey"), prop: "api_key_masked", slot: "api_key_masked" },
  { label: t("image_workbench.service.remark"), prop: "remark", span: 2 },
  { label: t("image_workbench.common.createdTime"), prop: "created_time" },
  { label: t("image_workbench.common.updatedTime"), prop: "updated_time" },
]);

const initialFormData: ServiceForm = {
  id: undefined,
  name: "",
  dialect: "ark",
  base_url: "",
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
      label: t("image_workbench.service.name"),
      key: "name",
      type: "input",
      span: 24,
      props: {
        placeholder: t("image_workbench.service.namePlaceholder"),
        maxlength: 100,
        clearable: true,
      },
    },
    { label: t("image_workbench.service.dialect"), key: "dialect", type: "radiogroup", span: 24 },
    {
      label: t("image_workbench.service.baseUrl"),
      key: "base_url",
      type: "input",
      span: 24,
      props: {
        placeholder: t("image_workbench.service.baseUrlPlaceholder"),
        maxlength: 255,
        clearable: true,
      },
    },
    // 完整前缀语义与渠道评估「平台」的 base_url 不同，给出显式提示
    { label: "", key: "base_url_tip", span: 24 },
    {
      label: t("image_workbench.service.apiKey"),
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
          ? t("image_workbench.service.apiKeyEditPlaceholder")
          : t("image_workbench.service.apiKeyCreatePlaceholder"),
      },
    },
    { label: t("image_workbench.common.status"), key: "status", type: "radiogroup", span: 24 },
    {
      label: t("image_workbench.service.remark"),
      key: "remark",
      type: "input",
      span: 24,
      props: {
        type: "textarea",
        rows: 2,
        maxlength: 500,
        showWordLimit: true,
        placeholder: t("image_workbench.service.remarkPlaceholder"),
      },
    },
    { label: t("image_workbench.common.testConnection"), key: "test_connection", span: 24 },
  ];
});

type RuleCallback = (error?: Error) => void;

/** base_url 必须是完整 API 前缀：须带协议头，且不得以 / 结尾（后端同样归一化尾部斜杠） */
function baseUrlValidator(_rule: unknown, value: unknown, callback: RuleCallback) {
  const text = String(value ?? "").trim();
  if (!text) {
    callback();
    return;
  }
  if (!/^https?:\/\//i.test(text)) {
    callback(new Error(t("image_workbench.service.baseUrlInvalid")));
    return;
  }
  try {
    const parsed = new URL(text);
    const segments = parsed.pathname.split("/").filter(Boolean);
    if (segments.length === 0) {
      callback(new Error(t("image_workbench.service.baseUrlVersionMissing")));
      return;
    }
  } catch {
    callback(new Error(t("image_workbench.service.baseUrlInvalid")));
    return;
  }
  callback();
}

const rules = computed(() => ({
  name: [{ required: true, message: t("image_workbench.service.nameRequired"), trigger: "blur" }],
  dialect: [
    { required: true, message: t("image_workbench.service.dialectRequired"), trigger: "change" },
  ],
  base_url: [
    { required: true, message: t("image_workbench.service.baseUrlRequired"), trigger: "blur" },
    { validator: baseUrlValidator, trigger: "blur" },
  ],
}));

const { submitLoading, handleCloseDialog, handleOpenDialog, handleSubmit } =
  useCrudForm<ServiceForm>({
    formData,
    initialFormData,
    dialogVisible,
    dataFormRef,
    formRenderKey,
    detailApi: ServiceAPI.detailService,
    createApi: ServiceAPI.createService,
    updateApi: ServiceAPI.updateService,
    detailFormData,
    titles: {
      create: t("image_workbench.service.createTitle"),
      update: t("image_workbench.service.updateTitle"),
      detail: t("image_workbench.service.detailTitle"),
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

// ── 测试连接 ───
const testLoading = ref(false);
const testResult = ref<TestConnectionResult | null>(null);

watch(
  () => dialogVisible.visible,
  (visible) => {
    if (!visible) {
      testResult.value = null;
      testLoading.value = false;
    }
  }
);

/** 用弹窗当前所填凭证测连通性；未填的字段留空由后端回退库内已存值 */
async function handleTestConnection() {
  const payload: TestConnectionPayload = {
    service_id: formData.value.id,
    dialect: formData.value.dialect,
    base_url: formData.value.base_url?.trim() || undefined,
    api_key: formData.value.api_key?.trim() || undefined,
  };
  if (!payload.base_url && !payload.service_id) {
    ElMessage.warning(t("image_workbench.service.baseUrlRequired"));
    return;
  }
  testLoading.value = true;
  testResult.value = null;
  try {
    const response = await ServiceAPI.testConnection(payload);
    testResult.value = response.data.data;
  } catch {
    // 请求失败已由拦截器提示
  } finally {
    testLoading.value = false;
  }
}

async function testConnectionRow(row: ServiceTable) {
  const loadingMsg = ElMessage({
    message: t("image_workbench.common.testing"),
    type: "info",
    duration: 0,
  });
  try {
    const response = await ServiceAPI.testSavedConnection(row.id!);
    const result = response.data.data;
    if (result?.success) {
      ElMessage.success(result.message);
    } else {
      ElMessage.error(result?.message || t("image_workbench.common.testFailed"));
    }
  } catch {
    // 请求失败已由拦截器提示
  } finally {
    loadingMsg.close();
  }
}

// ── 行操作 / 删除 ───
function buildServiceRowActions(row: ServiceTable): TableOperationAction[] {
  return [
    {
      key: "detail",
      label: t("image_workbench.common.detail"),
      artType: "view",
      perm: "image_workbench:service:query",
      run: () => {
        if (row.id != null) void handleOpenDialog("detail", row.id);
      },
    },
    {
      key: "test",
      label: t("image_workbench.common.testConnection"),
      artType: "view",
      icon: "ri:links-line",
      perm: "image_workbench:service:edit",
      run: () => {
        void testConnectionRow(row);
      },
    },
    {
      key: "edit",
      label: t("image_workbench.common.edit"),
      artType: "edit",
      perm: "image_workbench:service:edit",
      run: () => {
        if (row.id != null) void handleOpenDialog("update", row.id);
      },
    },
    {
      key: "delete",
      label: t("image_workbench.common.delete"),
      artType: "delete",
      perm: "image_workbench:service:delete",
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
    await confirmDelete(t("image_workbench.service.deleteConfirm", { name: row.name }));
    await ServiceAPI.deleteService([row.id!]);
    faTableRef.value?.elTableRef?.clearSelection();
    if (currentServiceId.value === row.id) {
      currentServiceId.value = 0;
      currentServiceName.value = "";
      hasAutoSelected.value = false;
    }
    await refreshRemove();
  } catch {
    // 用户取消，或被引用保护拒绝（已由拦截器提示可改为停用）
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
    // 用户取消，或被引用保护拒绝（已由拦截器提示可改为停用）
  } finally {
    batchDeleting.value = false;
  }
}
</script>
