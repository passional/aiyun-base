<!-- 渠道评估 - 平台管理：列表 / 表单 / 测试连接 / 基准标识（凭证仅脱敏展示） -->
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
            :perm-create="['channel_eval:platform:add']"
            :perm-delete="['channel_eval:platform:delete']"
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
      :title="dialogTitle"
      width="760px"
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
          <template #type="{ row }">
            <FaStatusTag
              :type="row?.type === 'admin' ? 'primary' : 'info'"
              :label="platformTypeLabel(row?.type as PlatformType)"
            />
          </template>
          <template #is_baseline="{ row }">
            <FaStatusTag
              :type="row?.is_baseline ? 'success' : 'info'"
              :label="
                row?.is_baseline ? $t('channel_eval.common.yes') : $t('channel_eval.common.no')
              "
            />
          </template>
          <template #status="{ row }">
            <FaStatusTag
              :type="row?.status === 0 ? 'success' : 'danger'"
              :label="statusLabel(row?.status as number)"
            />
          </template>
          <template #admin_key_masked="{ row }">
            {{ orDash(row?.admin_key_masked as string | null | undefined) }}
          </template>
          <template #test_key_masked="{ row }">
            {{ orDash(row?.test_key_masked as string | null | undefined) }}
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
          <template #type>
            <ElRadioGroup v-model="formData.type">
              <ElRadio value="admin">{{ $t("channel_eval.common.platformAdmin") }}</ElRadio>
              <ElRadio value="user">{{ $t("channel_eval.common.platformUser") }}</ElRadio>
            </ElRadioGroup>
          </template>
          <template #is_baseline>
            <ElSwitch v-model="formData.is_baseline" />
          </template>
          <template #status>
            <ElRadioGroup v-model="formData.status">
              <ElRadio :value="0">{{ $t("channel_eval.common.enable") }}</ElRadio>
              <ElRadio :value="1">{{ $t("channel_eval.common.disable") }}</ElRadio>
            </ElRadioGroup>
          </template>
          <template #test_connection>
            <div class="flex flex-wrap items-center gap-2">
              <ElButton
                :loading="testLoading"
                :disabled="testLoading"
                @click="handleTestConnection"
              >
                {{ $t("channel_eval.common.testConnection") }}
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
import PlatformAPI, {
  type PlatformForm,
  type PlatformTable,
  type PlatformType,
  type TestConnectionPayload,
  type TestConnectionResult,
} from "@/api/module_channel_eval/platform";
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

defineOptions({
  name: "ChannelEvalPlatform",
  inheritAttrs: false,
});

const { t, locale } = useI18n();

// ─── 通用展示辅助 ───
function orDash(value?: string | null) {
  return value || "—";
}

function platformTypeLabel(value?: PlatformType) {
  return value === "user"
    ? t("channel_eval.common.platformUser")
    : t("channel_eval.common.platformAdmin");
}

function statusLabel(value?: number) {
  return value === 1 ? t("channel_eval.common.disable") : t("channel_eval.common.enable");
}

// ─── 搜索 ───
type PlatformSearchForm = {
  name?: string;
  type?: PlatformType;
  is_baseline?: boolean;
  status?: number;
};

const searchForm = ref<PlatformSearchForm>({
  name: undefined,
  type: undefined,
  is_baseline: undefined,
  status: undefined,
});

const showSearchBar = ref(true);
const searchBarRef = ref<InstanceType<typeof FaSearchBar> | null>(null);
const searchBarRules: Record<string, unknown> = {};

const platformTypeOptions = computed(() => [
  { label: t("channel_eval.common.platformAdmin"), value: "admin" as PlatformType },
  { label: t("channel_eval.common.platformUser"), value: "user" as PlatformType },
]);

const yesNoOptions = computed(() => [
  { label: t("channel_eval.common.yes"), value: true },
  { label: t("channel_eval.common.no"), value: false },
]);

const statusOptions = computed(() => [
  { label: t("channel_eval.common.enable"), value: 0 },
  { label: t("channel_eval.common.disable"), value: 1 },
]);

const searchItems = computed<SearchFormItem[]>(() => [
  {
    label: t("channel_eval.platform.name"),
    key: "name",
    type: "input",
    placeholder: t("channel_eval.platform.namePlaceholder"),
    clearable: true,
    span: 6,
  },
  {
    label: t("channel_eval.platform.type"),
    key: "type",
    type: "select",
    props: {
      placeholder: t("channel_eval.platform.typePlaceholder"),
      options: platformTypeOptions.value,
      clearable: true,
    },
    span: 6,
  },
  {
    label: t("channel_eval.common.isBaseline"),
    key: "is_baseline",
    type: "select",
    props: {
      placeholder: t("channel_eval.common.selectPlaceholder"),
      options: yesNoOptions.value,
      clearable: true,
    },
    span: 6,
  },
  {
    label: t("channel_eval.common.status"),
    key: "status",
    type: "select",
    props: {
      placeholder: t("channel_eval.common.selectPlaceholder"),
      options: statusOptions.value,
      clearable: true,
    },
    span: 6,
  },
]);

// ─── 表格 ───
const ORDER_BY_DESC = JSON.stringify([{ id: "desc" }]);

function buildPlatformColumns(): ColumnOption<PlatformTable>[] {
  return [
    { type: "selection", width: 48, fixed: "left" },
    { type: "globalIndex", width: 56, label: t("channel_eval.common.index") },
    {
      prop: "name",
      label: t("channel_eval.platform.name"),
      minWidth: 150,
      showOverflowTooltip: true,
    },
    {
      prop: "type",
      label: t("channel_eval.platform.type"),
      width: 130,
      status: {
        admin: { type: "primary", text: t("channel_eval.common.platformAdmin") },
        user: { type: "info", text: t("channel_eval.common.platformUser") },
      },
    },
    {
      prop: "base_url",
      label: t("channel_eval.platform.baseUrl"),
      minWidth: 200,
      showOverflowTooltip: true,
    },
    {
      prop: "is_baseline",
      label: t("channel_eval.common.isBaseline"),
      width: 100,
      status: {
        true: { type: "success", text: t("channel_eval.common.yes") },
        false: { type: "info", text: t("channel_eval.common.no") },
      },
    },
    {
      prop: "status",
      label: t("channel_eval.common.status"),
      width: 90,
      status: {
        0: { type: "success", text: t("channel_eval.common.enable") },
        1: { type: "danger", text: t("channel_eval.common.disable") },
      },
    },
    {
      prop: "admin_key_masked",
      label: t("channel_eval.platform.adminKey"),
      minWidth: 120,
      formatter: (row: PlatformTable) => orDash(row.admin_key_masked),
    },
    {
      prop: "test_key_masked",
      label: t("channel_eval.platform.testKey"),
      minWidth: 120,
      formatter: (row: PlatformTable) => orDash(row.test_key_masked),
    },
    {
      prop: "remark",
      label: t("channel_eval.platform.remark"),
      minWidth: 140,
      showOverflowTooltip: true,
    },
    {
      prop: "created_time",
      label: t("channel_eval.common.createdTime"),
      width: 168,
      showOverflowTooltip: true,
    },
    {
      prop: "updated_time",
      label: t("channel_eval.common.updatedTime"),
      width: 168,
      showOverflowTooltip: true,
    },
    {
      prop: "operation",
      label: t("channel_eval.common.operation"),
      width: 200,
      fixed: "right",
      align: "center",
      formatter: (row: PlatformTable) => formatPlatformOperationCell(row),
    },
  ];
}

const faTableRef = ref<{ elTableRef?: { clearSelection: () => void } } | null>(null);

const { selectedIds, batchDeleting, onTableSelectionChange } = useTableSelection<PlatformTable>();

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
    apiFn: PlatformAPI.listPlatform,
    apiParams: { page_no: 1, page_size: 10, order_by: ORDER_BY_DESC },
    columnsFactory: resolveStatusColumns<PlatformTable>(() => buildPlatformColumns()),
  },
});

// 语言切换后刷新列头文案（列配置工厂只在初始化时执行一次）
watch(locale, () => {
  updateColumn?.(buildPlatformColumns().map((col) => ({ prop: getColumnKey(col), updates: col })));
});

function buildPlatformReplaceParams(p: PlatformSearchForm): Record<string, unknown> {
  return {
    name: p.name,
    type: p.type,
    is_baseline: p.is_baseline,
    status: p.status,
    order_by: ORDER_BY_DESC,
  };
}

async function handleSearchBarSearch(params: PlatformSearchForm) {
  await searchBarRef.value?.validate?.();
  replaceSearchParams(buildPlatformReplaceParams(params));
  await getData();
}

async function onResetSearch() {
  searchForm.value = {
    name: undefined,
    type: undefined,
    is_baseline: undefined,
    status: undefined,
  };
  await resetSearchParams();
}

// ─── 对话框 / 表单 ───
const { dialogVisible } = useCrudDialog();

const detailFormData = ref<Partial<PlatformTable>>({});

const dialogTitle = computed(() => {
  const map = {
    create: t("channel_eval.platform.createTitle"),
    update: t("channel_eval.platform.updateTitle"),
    detail: t("channel_eval.platform.detailTitle"),
  } as const;
  return map[dialogVisible.type];
});

const detailItems = computed<DescriptionsItem[]>(() => [
  { label: t("channel_eval.platform.name"), prop: "name" },
  { label: t("channel_eval.platform.type"), prop: "type", slot: "type" },
  { label: t("channel_eval.platform.baseUrl"), prop: "base_url" },
  { label: t("channel_eval.common.isBaseline"), prop: "is_baseline", slot: "is_baseline" },
  { label: t("channel_eval.common.status"), prop: "status", slot: "status" },
  {
    label: t("channel_eval.platform.adminKey"),
    prop: "admin_key_masked",
    slot: "admin_key_masked",
  },
  { label: t("channel_eval.platform.testKey"), prop: "test_key_masked", slot: "test_key_masked" },
  { label: t("channel_eval.platform.remark"), prop: "remark", span: 2 },
  { label: t("channel_eval.common.createdTime"), prop: "created_time" },
  { label: t("channel_eval.common.updatedTime"), prop: "updated_time" },
]);

const initialFormData: PlatformForm = {
  id: undefined,
  name: "",
  type: "admin",
  base_url: "",
  admin_key: undefined,
  admin_user_id: undefined,
  test_key: undefined,
  is_baseline: false,
  status: 0,
  remark: undefined,
};

const formData = ref<PlatformForm>({ ...initialFormData });

const formRenderKey = ref(0);
const dataFormRef = ref<InstanceType<typeof FaForm> | null>(null);

const isUserType = computed(() => formData.value.type === "user");

const formItems = computed<FormItem[]>(() => {
  const isEdit = dialogVisible.type === "update";
  return [
    {
      label: t("channel_eval.platform.name"),
      key: "name",
      type: "input",
      span: 24,
      props: {
        placeholder: t("channel_eval.platform.namePlaceholder"),
        maxlength: 100,
        clearable: true,
      },
    },
    { label: t("channel_eval.platform.type"), key: "type", type: "radiogroup", span: 24 },
    {
      label: t("channel_eval.platform.baseUrl"),
      key: "base_url",
      type: "input",
      span: 24,
      props: {
        placeholder: t("channel_eval.platform.baseUrlPlaceholder"),
        maxlength: 255,
        clearable: true,
      },
    },
    {
      label: t("channel_eval.platform.adminKey"),
      key: "admin_key",
      type: "input",
      span: 24,
      hidden: isUserType.value,
      props: {
        type: "password",
        showPassword: true,
        autocomplete: "new-password",
        clearable: true,
        maxlength: 512,
        placeholder: isEdit
          ? t("channel_eval.platform.adminKeyEditPlaceholder")
          : t("channel_eval.platform.adminKeyCreatePlaceholder"),
      },
    },
    {
      label: t("channel_eval.platform.adminUserId"),
      key: "admin_user_id",
      type: "input",
      span: 24,
      hidden: isUserType.value,
      props: {
        maxlength: 64,
        clearable: true,
        placeholder: isEdit
          ? t("channel_eval.platform.adminUserIdEditPlaceholder")
          : t("channel_eval.platform.adminUserIdCreatePlaceholder"),
      },
    },
    {
      label: t("channel_eval.platform.testKey"),
      key: "test_key",
      type: "input",
      span: 24,
      props: {
        type: "password",
        showPassword: true,
        autocomplete: "new-password",
        clearable: true,
        maxlength: 512,
        placeholder: isEdit
          ? t("channel_eval.platform.testKeyEditPlaceholder")
          : t("channel_eval.platform.testKeyCreatePlaceholder"),
      },
    },
    { label: t("channel_eval.common.isBaseline"), key: "is_baseline", type: "switch", span: 24 },
    { label: t("channel_eval.common.status"), key: "status", type: "radiogroup", span: 24 },
    {
      label: t("channel_eval.platform.remark"),
      key: "remark",
      type: "input",
      span: 24,
      props: {
        type: "textarea",
        rows: 2,
        maxlength: 500,
        showWordLimit: true,
        placeholder: t("channel_eval.platform.remarkPlaceholder"),
      },
    },
    { label: t("channel_eval.common.testConnection"), key: "test_connection", span: 24 },
  ];
});

type RuleCallback = (error?: Error) => void;

function baseUrlValidator(_rule: unknown, value: unknown, callback: RuleCallback) {
  const text = String(value ?? "").trim();
  if (!text) return callback(); // 必填由 required 规则负责
  if (!/^https?:\/\//i.test(text)) {
    callback(new Error(t("channel_eval.platform.baseUrlInvalid")));
    return;
  }
  callback();
}

function adminKeyValidator(_rule: unknown, value: unknown, callback: RuleCallback) {
  const requiredNow = dialogVisible.type === "create" && formData.value.type === "admin";
  if (requiredNow && !String(value ?? "").trim()) {
    callback(new Error(t("channel_eval.platform.adminKeyRequired")));
    return;
  }
  callback();
}

function testKeyValidator(_rule: unknown, value: unknown, callback: RuleCallback) {
  const requiredNow = dialogVisible.type === "create";
  if (requiredNow && !String(value ?? "").trim()) {
    callback(new Error(t("channel_eval.platform.testKeyRequired")));
    return;
  }
  callback();
}

const rules = computed(() => ({
  name: [{ required: true, message: t("channel_eval.platform.nameRequired"), trigger: "blur" }],
  type: [{ required: true, message: t("channel_eval.platform.typeRequired"), trigger: "change" }],
  base_url: [
    { required: true, message: t("channel_eval.platform.baseUrlRequired"), trigger: "blur" },
    { validator: baseUrlValidator, trigger: "blur" },
  ],
  admin_key: [{ validator: adminKeyValidator, trigger: "blur" }],
  test_key: [{ validator: testKeyValidator, trigger: "blur" }],
}));

const { submitLoading, handleCloseDialog, handleOpenDialog, handleSubmit } =
  useCrudForm<PlatformForm>({
    formData,
    initialFormData,
    dialogVisible,
    dataFormRef,
    formRenderKey,
    detailApi: PlatformAPI.detailPlatform,
    createApi: PlatformAPI.createPlatform,
    updateApi: PlatformAPI.updatePlatform,
    detailFormData,
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

// ─── 测试连接 ───
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

async function handleTestConnection() {
  const payload: TestConnectionPayload = {
    type: formData.value.type,
    base_url: formData.value.base_url?.trim() || undefined,
    admin_key: formData.value.admin_key?.trim() || undefined,
    admin_user_id: formData.value.admin_user_id?.trim() || undefined,
    test_key: formData.value.test_key?.trim() || undefined,
    platform_id: formData.value.id,
  };
  if (!payload.base_url && !payload.platform_id) {
    ElMessage.warning(t("channel_eval.platform.baseUrlRequired"));
    return;
  }
  testLoading.value = true;
  testResult.value = null;
  try {
    const response = await PlatformAPI.testConnection(payload);
    testResult.value = response.data.data;
  } catch {
    // 请求失败已由拦截器提示
  } finally {
    testLoading.value = false;
  }
}

async function testConnectionRow(row: PlatformTable) {
  const loadingMsg = ElMessage({
    message: t("channel_eval.common.testing"),
    type: "info",
    duration: 0,
  });
  try {
    const response = await PlatformAPI.testSavedConnection(row.id!);
    const result = response.data.data;
    if (result.success) {
      ElMessage.success(result.message);
    } else {
      ElMessage.error(result.message);
    }
  } catch {
    // 请求失败已由拦截器提示
  } finally {
    loadingMsg.close();
  }
}

// ─── 行操作 / 删除 ───
function buildPlatformRowActions(row: PlatformTable): TableOperationAction[] {
  return [
    {
      key: "detail",
      label: t("channel_eval.common.detail"),
      artType: "view",
      perm: "channel_eval:platform:query",
      run: () => {
        if (row.id != null) void handleOpenDialog("detail", row.id);
      },
    },
    {
      key: "test",
      label: t("channel_eval.common.testConnection"),
      artType: "view",
      icon: "ri:links-line",
      perm: "channel_eval:platform:query",
      run: () => {
        if (row.id != null) void testConnectionRow(row);
      },
    },
    {
      key: "edit",
      label: t("channel_eval.common.edit"),
      artType: "edit",
      perm: "channel_eval:platform:edit",
      run: () => {
        if (row.id != null) void handleOpenDialog("update", row.id);
      },
    },
    {
      key: "delete",
      label: t("channel_eval.common.delete"),
      artType: "delete",
      perm: "channel_eval:platform:delete",
      run: () => {
        if (row.id != null) void deletePlatformRow(row);
      },
    },
  ];
}

function formatPlatformOperationCell(row: PlatformTable) {
  return renderTableOperationCell(buildPlatformRowActions(row), {
    wrapperClass: "inline-flex flex-wrap items-center justify-end gap-1",
  });
}

async function deletePlatformRow(row: PlatformTable) {
  try {
    await confirmDelete(t("channel_eval.platform.deleteConfirm", { name: row.name }));
    await PlatformAPI.deletePlatform([row.id!]);
    faTableRef.value?.elTableRef?.clearSelection();
    await refreshRemove();
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
      (data.value as PlatformTable[]).filter((row) => ids.includes(row.id!)).map((row) => row.name)
    );
    batchDeleting.value = true;
    await PlatformAPI.deletePlatform(ids);
    faTableRef.value?.elTableRef?.clearSelection();
    await refreshRemove();
  } catch {
    // 用户取消或删除被拒绝（已由拦截器提示）
  } finally {
    batchDeleting.value = false;
  }
}
</script>
