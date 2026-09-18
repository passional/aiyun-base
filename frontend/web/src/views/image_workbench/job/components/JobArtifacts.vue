<!--
  绘图工作台 - 任务产物的逐张呈现（底图 + 若干图层）。

  产物是独立实体（design D7）：每张单独下载，不再有「整包下载」——一次任务可以产出
  底图与多个图层，「任务 → 一个文件」的假设不成立。底图在前，图层按层级升序（后端已排好序）。
-->
<template>
  <div v-if="artifacts.length === 0" class="text-xs text-(--el-text-color-secondary)">
    {{ t("image_workbench.job.noArtifact") }}
  </div>
  <div v-else class="grid grid-cols-2 gap-3 md:grid-cols-3">
    <div
      v-for="artifact in artifacts"
      :key="artifact.id"
      class="rounded border border-(--el-border-color) p-2"
    >
      <div class="mb-1 flex items-center justify-between gap-1">
        <span class="truncate text-xs font-medium">{{ titleOf(artifact) }}</span>
        <ElTag v-if="artifact.is_base" size="small" type="success">
          {{ t("image_workbench.job.baseLayer") }}
        </ElTag>
        <ElTag v-else size="small">
          {{ t("image_workbench.job.layerIndex", { index: artifact.z_index ?? 0 }) }}
        </ElTag>
      </div>

      <ElImage
        v-if="artifact.image_url"
        :src="artifact.image_url"
        fit="contain"
        class="h-28 w-full rounded"
        :preview-src-list="[artifact.image_url]"
        preview-teleported
      />
      <div
        v-else
        class="flex h-28 items-center justify-center rounded bg-(--el-fill-color-light) text-xs text-(--el-text-color-secondary)"
      >
        {{ t("image_workbench.job.noPreview") }}
      </div>

      <div class="mt-1 space-y-0.5 text-xs text-(--el-text-color-secondary)">
        <div v-if="artifact.description" class="line-clamp-2">{{ artifact.description }}</div>
        <div v-if="artifact.image_size">{{ artifact.image_size }}</div>
        <div v-if="boundingText(artifact)">{{ boundingText(artifact) }}</div>
      </div>

      <ElButton
        v-hasPerm="['image_workbench:job:download']"
        size="small"
        class="mt-1 w-full"
        :icon="Download"
        :loading="downloadingId === artifact.id"
        @click="download(artifact)"
      >
        {{ t("image_workbench.job.download") }}
      </ElButton>
    </div>
  </div>
</template>

<script setup lang="ts">
import { Download } from "@element-plus/icons-vue";
import { saveAs } from "file-saver";
import { ElMessage } from "element-plus";
import JobAPI, { type JobArtifact } from "@/api/module_image_workbench/job";
import { formatValue } from "../../values";

defineOptions({
  name: "ImageWorkbenchJobArtifacts",
  inheritAttrs: false,
});

const props = defineProps<{
  /** 任务主键：下载按「任务 + 产物」定位 */
  jobId?: number | null;
  artifacts?: JobArtifact[];
}>();

const { t } = useI18n();

const artifacts = computed(() => props.artifacts ?? []);
const downloadingId = ref<number | null>(null);

function titleOf(artifact: JobArtifact) {
  if (artifact.name) return artifact.name;
  return artifact.is_base
    ? t("image_workbench.job.baseLayer")
    : t("image_workbench.job.layerIndex", { index: artifact.z_index ?? 0 });
}

/** 位置框：优先归一化表示（与交互编辑用的是同一个 0–999 坐标系），否则用绝对表示 */
function boundingText(artifact: JobArtifact) {
  const box = artifact.bounding_box;
  if (!box) return "";
  const values = box.normalized ?? box.absolute;
  if (!Array.isArray(values) || values.length === 0) return "";
  return `${t("image_workbench.job.boundingBox")}：${values.map((item) => formatValue(item)).join(", ")}`;
}

async function download(artifact: JobArtifact) {
  if (!props.jobId) return;
  downloadingId.value = artifact.id;
  try {
    const response = await JobAPI.downloadArtifact(props.jobId, artifact.id);
    saveAs(
      response.data as Blob,
      filenameFromDisposition(
        response.headers?.["content-disposition"],
        `${props.jobId}-${titleOf(artifact)}.png`
      )
    );
    ElMessage.success(t("image_workbench.job.downloadStarted"));
  } catch {
    ElMessage.error(t("image_workbench.job.downloadFailed"));
  } finally {
    downloadingId.value = null;
  }
}

function filenameFromDisposition(header: string | undefined, fallback: string): string {
  if (!header) return fallback;
  const utf8 = /filename\*=UTF-8''([^;]+)/i.exec(header);
  if (utf8?.[1]) return decodeURIComponent(utf8[1]);
  const plain = /filename="?([^";]+)"?/i.exec(header);
  return plain?.[1] ?? fallback;
}
</script>
