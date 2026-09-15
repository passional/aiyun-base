<!-- 渠道评估 - 对比报告编辑器：选择多次运行 + 基准运行（默认基准渠道最新成功）+ 备注 -->
<template>
  <FaDialog
    v-model="visible"
    :title="dialogTitle"
    width="800px"
    dialog-class="crud-embed-dialog"
    modal-class="crud-embed-dialog"
    :form-mode="reportId ? 'update' : 'create'"
    :confirm-loading="submitting"
    @cancel="visible = false"
    @close="visible = false"
    @confirm="submit"
  >
    <div v-loading="loading">
      <ElAlert
        v-if="!loading && baselinePlatformIds.length === 0"
        class="mb-3"
        type="warning"
        :closable="false"
        :title="$t('channel_eval.report.noBaselinePlatform')"
      />
      <FaForm
        ref="formRef"
        v-model="form"
        :items="formItems"
        :rules="rules"
        label-suffix=":"
        :label-width="130"
        label-position="right"
        :span="24"
        :show-reset="false"
        :show-submit="false"
      >
        <template #run_ids>
          <ElSelect
            v-model="form.run_ids"
            multiple
            filterable
            collapse-tags
            collapse-tags-tooltip
            class="w-full"
            :placeholder="$t('channel_eval.report.runIdsPlaceholder')"
          >
            <ElOption
              v-for="run in runOptions"
              :key="run.id"
              :label="runOptionLabel(run)"
              :value="run.id!"
            />
          </ElSelect>
        </template>
        <template #baseline_run_id>
          <ElSelect
            v-model="form.baseline_run_id"
            filterable
            clearable
            class="w-full"
            :placeholder="$t('channel_eval.report.baselinePlaceholder')"
          >
            <ElOption
              v-for="run in baselineOptions"
              :key="run.id"
              :label="runOptionLabel(run)"
              :value="run.id!"
            />
          </ElSelect>
          <div class="mt-1 text-xs text-g-500">{{ $t("channel_eval.report.baselineHint") }}</div>
        </template>
      </FaForm>
    </div>
  </FaDialog>
</template>

<script setup lang="ts">
import PlatformAPI, { type PlatformTable } from "@/api/module_channel_eval/platform";
import RunAPI, { type RunTable } from "@/api/module_channel_eval/run";
import ReportAPI, { type ReportForm } from "@/api/module_channel_eval/report";
import type { FormItem } from "@/components/forms/fa-form/index.vue";
import FaForm from "@/components/forms/fa-form/index.vue";
import { runStatusText } from "../../run/status";

defineOptions({ name: "ReportEditor" });

const visible = defineModel<boolean>({ default: false });
const props = defineProps<{ reportId?: number }>();
const emit = defineEmits<{ saved: [] }>();

const { t } = useI18n();

const ORDER_BY_DESC = JSON.stringify([{ id: "desc" }]);

const loading = ref(false);
const submitting = ref(false);
const formRef = ref<InstanceType<typeof FaForm> | null>(null);

const runs = ref<RunTable[]>([]);
const platforms = ref<PlatformTable[]>([]);

const form = ref<ReportForm>({
  id: undefined,
  name: "",
  run_ids: [],
  baseline_run_id: null,
  note: undefined,
});

const dialogTitle = computed(() =>
  props.reportId ? t("channel_eval.report.updateTitle") : t("channel_eval.report.createTitle")
);

const baselinePlatformIds = computed(() =>
  platforms.value.filter((item) => item.is_baseline).map((item) => item.id!)
);

const runOptions = computed(() => runs.value);

const baselineOptions = computed(() =>
  runs.value.filter(
    (run) => run.status === "success" && baselinePlatformIds.value.includes(run.platform_id)
  )
);

const defaultBaselineRunId = computed(() => baselineOptions.value[0]?.id ?? null);

function runOptionLabel(run: RunTable) {
  const platform = run.platform_name || `#${run.platform_id}`;
  return `#${run.id} · ${platform} · ${runStatusText(run.status)} · ${run.combo_count} ${t("channel_eval.run.comboCount")}`;
}

const formItems = computed<FormItem[]>(() => [
  {
    label: t("channel_eval.report.name"),
    key: "name",
    type: "input",
    span: 24,
    props: {
      placeholder: t("channel_eval.report.namePlaceholder"),
      maxlength: 100,
      clearable: true,
    },
  },
  { label: t("channel_eval.report.runIds"), key: "run_ids", span: 24 },
  { label: t("channel_eval.report.baselineRun"), key: "baseline_run_id", span: 24 },
  {
    label: t("channel_eval.report.note"),
    key: "note",
    type: "input",
    span: 24,
    props: {
      type: "textarea",
      rows: 3,
      maxlength: 4000,
      showWordLimit: true,
      placeholder: t("channel_eval.report.notePlaceholder"),
    },
  },
]);

const rules = computed(() => ({
  name: [{ required: true, message: t("channel_eval.report.nameRequired"), trigger: "blur" }],
  run_ids: [
    {
      validator: (_rule: unknown, value: unknown, callback: (error?: Error) => void) => {
        const ids = Array.isArray(value) ? value : [];
        if (ids.length === 0) {
          callback(new Error(t("channel_eval.report.runIdsRequired")));
          return;
        }
        callback();
      },
      trigger: "change",
    },
  ],
}));

watch(visible, async (value) => {
  if (!value) return;
  loading.value = true;
  submitting.value = false;
  try {
    const [runsResponse, platformsResponse] = await Promise.all([
      RunAPI.listRun({ page_no: 1, page_size: 100, order_by: ORDER_BY_DESC }),
      PlatformAPI.platformOptions(),
    ]);
    runs.value = runsResponse.data.data.items ?? [];
    platforms.value = platformsResponse.data.data ?? [];

    if (props.reportId) {
      const detailResponse = await ReportAPI.detailReport(props.reportId);
      const data = detailResponse.data.data;
      form.value = {
        id: data.id,
        name: data.name,
        run_ids: [...(data.run_ids ?? [])],
        baseline_run_id: data.baseline_run_id ?? null,
        note: data.note ?? undefined,
      };
    } else {
      form.value = {
        id: undefined,
        name: "",
        run_ids: [],
        baseline_run_id: defaultBaselineRunId.value,
        note: undefined,
      };
    }
  } catch {
    // 请求失败已由拦截器提示
  } finally {
    loading.value = false;
  }
});

async function submit() {
  const formInstance = formRef.value;
  const valid = formInstance
    ? await (formInstance.validate as () => Promise<boolean>)().catch(() => false)
    : false;
  if (!valid) return;
  submitting.value = true;
  try {
    const payload: ReportForm = { ...form.value };
    if (props.reportId) {
      await ReportAPI.updateReport(props.reportId, payload);
    } else {
      await ReportAPI.createReport(payload);
    }
    visible.value = false;
    emit("saved");
  } catch {
    // 基准校验等业务错误已由拦截器提示，保持弹窗打开
  } finally {
    submitting.value = false;
  }
}
</script>
