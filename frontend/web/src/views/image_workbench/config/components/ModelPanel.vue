<!-- 绘图工作台 - 绘图模型：所选绘图服务下的两级子列表与能力声明编辑 -->
<template>
  <div v-if="!serviceId" class="flex h-full items-center justify-center">
    <ElEmpty :description="t('image_workbench.model.selectServiceHint')" />
  </div>

  <div v-else class="flex flex-col h-full overflow-hidden pl-2">
    <div class="mb-3 flex flex-wrap items-center gap-2">
      <span class="text-sm text-[var(--el-text-color-secondary)]">
        {{ t("image_workbench.model.belongService") }}：
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
        @selection-change="onTableSelectionChange"
        @pagination:size-change="handleSizeChange"
        @pagination:current-change="handleCurrentChange"
      />
    </ElCard>

    <FaDialog
      v-model="dialogVisible.visible"
      :title="dialogVisible.title"
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
          <template #status="{ row }">
            <FaStatusTag
              :type="asModel(row)?.status === 0 ? 'success' : 'danger'"
              :label="statusLabel(asModel(row)?.status)"
            />
          </template>
          <template #support_reference="{ row }">
            <FaStatusTag
              :type="asModel(row)?.capability?.support_reference ? 'success' : 'info'"
              :label="
                asModel(row)?.capability?.support_reference
                  ? $t('image_workbench.common.yes')
                  : $t('image_workbench.common.no')
              "
            />
          </template>
          <template #sizes="{ row }">
            {{ joinValues(asModel(row)?.capability?.sizes) }}
          </template>
          <template #output_formats="{ row }">
            {{ joinValues(asModel(row)?.capability?.output_formats) }}
          </template>
          <template #watermark_default="{ row }">
            <FaStatusTag
              :type="asModel(row)?.capability?.watermark_default ? 'warning' : 'info'"
              :label="
                asModel(row)?.capability?.watermark_default
                  ? $t('image_workbench.common.yes')
                  : $t('image_workbench.common.no')
              "
            />
          </template>
          <template #extra_params="{ row }">
            {{ formatExtraParams(asModel(row)?.capability?.extra_params) }}
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
          :label-width="140"
          label-position="right"
          :span="24"
          :gutter="16"
          :show-reset="false"
          :show-submit="false"
          class="crud-dialog-art-form"
        >
          <template #support_reference>
            <ElSwitch v-model="formData.capability.support_reference" />
          </template>
          <template #allow_custom_size>
            <ElSwitch v-model="formData.capability.allow_custom_size" />
          </template>
          <template #watermark_default>
            <ElSwitch v-model="formData.capability.watermark_default" />
          </template>
          <template #status>
            <ElRadioGroup v-model="formData.status">
              <ElRadio :value="0">{{ $t("image_workbench.common.enable") }}</ElRadio>
              <ElRadio :value="1">{{ $t("image_workbench.common.disable") }}</ElRadio>
            </ElRadioGroup>
          </template>
        </FaForm>
      </template>
    </FaDialog>
  </div>
</template>

<script setup lang="ts">
import { useCrudForm } from "@/hooks/core/useCrudForm";
import ModelAPI, {
  createDefaultCapability,
  type ModelCapability,
  type ModelForm,
  type ModelPageQuery,
  type ModelTable,
} from "@/api/module_image_workbench/model";
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

defineOptions({
  name: "ImageWorkbenchModelPanel",
  inheritAttrs: false,
});

const props = defineProps<{
  serviceId: number;
  serviceName: string;
}>();

const { t, locale } = useI18n();

/**
 * 弹窗表单：能力声明的额外参数以 JSON 文本编辑（提交前解析成对象）。
 * 能力声明的其余字段按 design D3 逐项对应。
 */
interface ModelDialogForm extends ModelForm {
  capability: ModelCapability;
  extra_params_text: string;
}

// ── 通用展示辅助 ───
function joinValues(values?: string[] | null) {
  return values && values.length > 0 ? values.join("、") : "—";
}

function statusLabel(value?: number) {
  return value === 1 ? t("image_workbench.common.disable") : t("image_workbench.common.enable");
}

/** 详情插槽的 row 由 FaDescriptions 以宽松类型下发，这里收回具体的列表项类型 */
function asModel(row: unknown): ModelTable | null {
  return (row as ModelTable | null) ?? null;
}

function formatExtraParams(extra?: Record<string, unknown> | null) {
  if (!extra || Object.keys(extra).length === 0) return "—";
  return JSON.stringify(extra);
}

// ── 搜索 ───
type ModelSearchForm = {
  model_id?: string;
  status?: number;
};

const searchForm = ref<ModelSearchForm>({ model_id: undefined, status: undefined });
const showSearchBar = ref(true);
const searchBarRef = ref<InstanceType<typeof FaSearchBar> | null>(null);
const searchBarRules: Record<string, unknown> = {};

const statusOptions = computed(() => [
  { label: t("image_workbench.common.enable"), value: 0 },
  { label: t("image_workbench.common.disable"), value: 1 },
]);

const searchItems = computed<SearchFormItem[]>(() => [
  {
    label: t("image_workbench.model.modelId"),
    key: "model_id",
    type: "input",
    placeholder: t("image_workbench.model.modelIdPlaceholder"),
    clearable: true,
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

function buildModelColumns(): ColumnOption<ModelTable>[] {
  return [
    { type: "selection", width: 48, fixed: "left" },
    { type: "globalIndex", width: 56, label: t("image_workbench.common.index") },
    {
      prop: "display_name",
      label: t("image_workbench.model.displayName"),
      minWidth: 140,
      showOverflowTooltip: true,
    },
    {
      prop: "model_id",
      label: t("image_workbench.model.modelId"),
      minWidth: 200,
      showOverflowTooltip: true,
    },
    {
      prop: "sizes",
      label: t("image_workbench.model.sizes"),
      minWidth: 130,
      formatter: (row: ModelTable) => joinValues(row.capability?.sizes),
    },
    {
      prop: "support_reference",
      label: t("image_workbench.model.supportReference"),
      width: 110,
      formatter: (row: ModelTable) =>
        row.capability?.support_reference
          ? t("image_workbench.common.yes")
          : t("image_workbench.common.no"),
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
      label: t("image_workbench.model.remark"),
      minWidth: 120,
      showOverflowTooltip: true,
    },
    {
      prop: "operation",
      label: t("image_workbench.common.operation"),
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

/** 列表接口按 service_id 过滤（两级结构：绘图模型永远属于某个绘图服务） */
async function fetchModelListMerged(params: Record<string, unknown>) {
  const query: ModelPageQuery = {
    page_no: Number(params.current) || Number(params.page_no) || 1,
    page_size: Number(params.size) || Number(params.page_size) || 10,
    service_id: props.serviceId,
    model_id: (params.model_id as string) || undefined,
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
    apiParams: { page_no: 1, page_size: 10, service_id: props.serviceId, order_by: ORDER_BY_ASC },
    columnsFactory: resolveStatusColumns<ModelTable>(() => buildModelColumns()),
  },
});

// 语言切换后刷新列头文案（列配置工厂只在初始化时执行一次）
watch(locale, () => {
  updateColumn?.(buildModelColumns().map((col) => ({ prop: getColumnKey(col), updates: col })));
});

async function handleSearchBarSearch(params: ModelSearchForm) {
  await searchBarRef.value?.validate?.();
  replaceSearchParams({
    model_id: params.model_id,
    status: params.status,
    order_by: ORDER_BY_ASC,
  });
  await getData();
}

async function onResetSearch() {
  searchForm.value = { model_id: undefined, status: undefined };
  await resetSearchParams();
}

// ── 对话框 / 表单 ───
const { dialogVisible } = useCrudDialog();

const detailFormData = ref<Partial<ModelTable>>({});

function createInitialFormData(): ModelDialogForm {
  return {
    id: undefined,
    service_id: props.serviceId,
    model_id: "",
    display_name: "",
    capability: createDefaultCapability(),
    status: 0,
    remark: undefined,
    extra_params_text: "",
  };
}

const initialFormData = createInitialFormData();
const formData = ref<ModelDialogForm>(createInitialFormData());

const formRenderKey = ref(0);
const dataFormRef = ref<InstanceType<typeof FaForm> | null>(null);

const supportsReference = computed(() => formData.value.capability.support_reference);

const outputFormatOptions = computed(() =>
  (formData.value.capability.output_formats ?? []).map((item) => ({ label: item, value: item }))
);

const detailItems = computed<DescriptionsItem[]>(() => [
  { label: t("image_workbench.model.displayName"), prop: "display_name" },
  { label: t("image_workbench.model.modelId"), prop: "model_id" },
  { label: t("image_workbench.common.status"), prop: "status", slot: "status" },
  { label: t("image_workbench.model.sizes"), prop: "sizes", slot: "sizes" },
  {
    label: t("image_workbench.model.supportReference"),
    prop: "support_reference",
    slot: "support_reference",
  },
  {
    label: t("image_workbench.model.maxReferenceImages"),
    prop: "capability.max_reference_images",
  },
  {
    label: t("image_workbench.model.outputFormats"),
    prop: "output_formats",
    slot: "output_formats",
  },
  {
    label: t("image_workbench.model.defaultOutputFormat"),
    prop: "capability.default_output_format",
  },
  {
    label: t("image_workbench.model.watermarkDefault"),
    prop: "watermark_default",
    slot: "watermark_default",
  },
  { label: t("image_workbench.model.extraParams"), prop: "extra_params", slot: "extra_params" },
  { label: t("image_workbench.model.remark"), prop: "remark", span: 2 },
  { label: t("image_workbench.common.createdTime"), prop: "created_time" },
  { label: t("image_workbench.common.updatedTime"), prop: "updated_time" },
]);

/**
 * 能力声明按 design D3 逐项声明。
 * 尺寸取值不做归一化翻译：上游 A 收档位字符串、上游 B 收固定像素枚举，
 * 这里按上游原文填写，页面不做「方/横/竖」映射。
 */
const formItems = computed<FormItem[]>(() => [
  {
    label: t("image_workbench.model.displayName"),
    key: "display_name",
    type: "input",
    span: 12,
    props: {
      placeholder: t("image_workbench.model.displayNamePlaceholder"),
      maxlength: 100,
      clearable: true,
    },
  },
  {
    label: t("image_workbench.model.modelId"),
    key: "model_id",
    type: "input",
    span: 12,
    props: {
      placeholder: t("image_workbench.model.modelIdPlaceholder"),
      maxlength: 128,
      clearable: true,
    },
  },
  { label: t("image_workbench.common.status"), key: "status", type: "radiogroup", span: 24 },
  {
    label: t("image_workbench.model.sizes"),
    key: "capability.sizes",
    type: "inputtag",
    span: 24,
    props: { placeholder: t("image_workbench.model.sizesPlaceholder") },
  },
  {
    label: t("image_workbench.model.allowCustomSize"),
    key: "capability.allow_custom_size",
    type: "switch",
    span: 12,
  },
  {
    label: t("image_workbench.model.supportReference"),
    key: "capability.support_reference",
    type: "switch",
    span: 12,
  },
  {
    label: t("image_workbench.model.maxReferenceImages"),
    key: "capability.max_reference_images",
    type: "number",
    span: 12,
    hidden: !supportsReference.value,
    props: { min: 0, max: 100, class: "w-full" },
  },
  {
    label: t("image_workbench.model.outputFormats"),
    key: "capability.output_formats",
    type: "inputtag",
    span: 24,
    props: { placeholder: t("image_workbench.model.outputFormatsPlaceholder") },
  },
  {
    label: t("image_workbench.model.defaultOutputFormat"),
    key: "capability.default_output_format",
    type: "select",
    span: 12,
    props: {
      placeholder: t("image_workbench.common.selectPlaceholder"),
      options: outputFormatOptions.value,
      clearable: true,
    },
  },
  {
    label: t("image_workbench.model.watermarkDefault"),
    key: "capability.watermark_default",
    type: "switch",
    span: 12,
  },
  {
    label: t("image_workbench.model.extraParams"),
    key: "extra_params_text",
    type: "input",
    span: 24,
    props: {
      type: "textarea",
      rows: 3,
      placeholder: t("image_workbench.model.extraParamsPlaceholder"),
    },
  },
  {
    label: t("image_workbench.model.remark"),
    key: "remark",
    type: "input",
    span: 24,
    props: {
      type: "textarea",
      rows: 2,
      maxlength: 500,
      showWordLimit: true,
      placeholder: t("image_workbench.model.remarkPlaceholder"),
    },
  },
]);

type RuleCallback = (error?: Error) => void;

/** 取值不得重复（后端同样拒绝 sizes / output_formats 的重复项） */
function uniqueArrayValidator(_rule: unknown, value: unknown, callback: RuleCallback) {
  const list = Array.isArray(value) ? (value as string[]) : [];
  const cleaned = list.filter((item) => item && String(item).trim());
  if (new Set(cleaned).size !== cleaned.length) {
    callback(new Error(t("image_workbench.model.duplicateValue")));
    return;
  }
  callback();
}

/** 默认输出格式必须落在可选输出格式内（后端同样校验，前端先拦） */
function defaultFormatValidator(_rule: unknown, value: unknown, callback: RuleCallback) {
  const formats = formData.value.capability.output_formats ?? [];
  const text = String(value ?? "").trim();
  if (!text) {
    callback();
    return;
  }
  if (formats.length === 0) {
    callback(new Error(t("image_workbench.model.outputFormatsRequired")));
    return;
  }
  if (!formats.includes(text)) {
    callback(new Error(t("image_workbench.model.defaultFormatInvalid")));
    return;
  }
  callback();
}

/** 额外参数以 JSON 对象填写，原样透传给上游 */
function extraParamsValidator(_rule: unknown, value: unknown, callback: RuleCallback) {
  const text = String(value ?? "").trim();
  if (!text) {
    callback();
    return;
  }
  try {
    const parsed: unknown = JSON.parse(text);
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
      callback(new Error(t("image_workbench.model.extraParamsInvalid")));
      return;
    }
  } catch {
    callback(new Error(t("image_workbench.model.extraParamsInvalid")));
    return;
  }
  callback();
}

const rules = computed(() => ({
  display_name: [
    { required: true, message: t("image_workbench.model.displayNameRequired"), trigger: "blur" },
  ],
  model_id: [
    { required: true, message: t("image_workbench.model.modelIdRequired"), trigger: "blur" },
  ],
  "capability.sizes": [{ validator: uniqueArrayValidator, trigger: "change" }],
  "capability.output_formats": [{ validator: uniqueArrayValidator, trigger: "change" }],
  "capability.default_output_format": [{ validator: defaultFormatValidator, trigger: "change" }],
  extra_params_text: [{ validator: extraParamsValidator, trigger: "blur" }],
}));

/** 详情 → 弹窗表单：能力声明与额外参数文本一并回填 */
function buildFormFromDetail(detail: ModelTable): ModelDialogForm {
  const extra = detail.capability?.extra_params;
  const hasExtra = !!extra && Object.keys(extra).length > 0;
  return {
    id: detail.id,
    service_id: detail.service_id,
    model_id: detail.model_id,
    display_name: detail.display_name,
    capability: { ...createDefaultCapability(), ...detail.capability },
    status: detail.status,
    remark: detail.remark,
    extra_params_text: hasExtra ? JSON.stringify(extra, null, 2) : "",
  };
}

/** 提交前还原成后端契约：额外参数文本 → 对象，能力声明整体覆盖 */
function toModelPayload(form: ModelDialogForm): ModelForm {
  const text = (form.extra_params_text ?? "").trim();
  return {
    service_id: form.service_id ?? props.serviceId,
    model_id: form.model_id,
    display_name: form.display_name,
    capability: {
      ...form.capability,
      extra_params: text ? (JSON.parse(text) as Record<string, unknown>) : {},
    },
    status: form.status,
    remark: form.remark,
  };
}

const { submitLoading, handleCloseDialog, handleOpenDialog, handleSubmit } =
  useCrudForm<ModelDialogForm>({
    formData,
    initialFormData,
    dialogVisible,
    dataFormRef,
    formRenderKey,
    createApi: (form) => ModelAPI.createModel(toModelPayload(form)),
    updateApi: (id, form) => ModelAPI.updateModel(id, toModelPayload(form)),
    onCreateSuccess: async () => {
      await refreshCreate();
    },
    onUpdateSuccess: async () => {
      await refreshUpdate();
    },
  });

/** 打开弹窗：详情取完整列表项，编辑额外回填能力声明与额外参数文本 */
async function openModelDialog(type: "detail" | "update", row: ModelTable) {
  if (row.id == null) return;
  const response = await ModelAPI.detailModel(row.id);
  const detail = response.data.data;
  if (!detail) return;
  if (type === "detail") {
    detailFormData.value = detail;
    dialogVisible.title = t("image_workbench.model.detailTitle");
  } else {
    formData.value = buildFormFromDetail(detail);
    dialogVisible.title = t("image_workbench.model.updateTitle");
  }
  dialogVisible.type = type;
  formRenderKey.value += 1;
  dialogVisible.visible = true;
}

async function handleAdd() {
  createLoading.value = true;
  try {
    await handleOpenDialog("create");
    // 能力声明取默认值；所属服务由父组件注入
    formData.value.capability = createDefaultCapability();
    formData.value.extra_params_text = "";
    formData.value.service_id = props.serviceId;
    dialogVisible.title = t("image_workbench.model.createTitle");
  } finally {
    createLoading.value = false;
  }
}

// ─── 行操作 / 删除 ───
function buildModelRowActions(row: ModelTable): TableOperationAction[] {
  return [
    {
      key: "detail",
      label: t("image_workbench.common.detail"),
      artType: "view",
      perm: "image_workbench:service:query",
      run: () => {
        void openModelDialog("detail", row);
      },
    },
    {
      key: "edit",
      label: t("image_workbench.common.edit"),
      artType: "edit",
      perm: "image_workbench:service:edit",
      run: () => {
        void openModelDialog("update", row);
      },
    },
    {
      key: "delete",
      label: t("image_workbench.common.delete"),
      artType: "delete",
      perm: "image_workbench:service:delete",
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
    await confirmDelete(t("image_workbench.model.deleteConfirm", { name: row.display_name }));
    await ModelAPI.deleteModel([row.id!]);
    faTableRef.value?.elTableRef?.clearSelection();
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
      (data.value as ModelTable[])
        .filter((row) => ids.includes(row.id!))
        .map((row) => row.display_name)
    );
    batchDeleting.value = true;
    await ModelAPI.deleteModel(ids);
    faTableRef.value?.elTableRef?.clearSelection();
    await refreshRemove();
  } catch {
    // 用户取消，或被引用保护拒绝（已由拦截器提示可改为停用）
  } finally {
    batchDeleting.value = false;
  }
}
</script>
