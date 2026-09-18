<!--
  绘图工作台 - 画布：在参考图、或图层拆分产物的**合成画面**上指定要修改的位置（design D1/D2）。

  两条设计决定落在这一处：

  1. **合成在前端做，导出走既有参考图上传接口**（D1）。画布本来就要渲染合成结果，导出几乎是
     免费的；而且坐标系**自洽**——合成图是用同一批位置框拼出来的，框必然落在该图层上，
     「发出去的就是看到的」由构造保证。为此把合成规则**集中在本文件的 `compose()` 一个
     函数里**（D10）：上游若推翻了合成规则，改动仅限此处。
  2. **合成严格按 `z_index` 升序，不假设「底图一定在最底」**（D2）。底图（`z_index` 为空）
     先铺底，各图层按层级升序依次叠放——上游也可能把背景本身拆成图层，一旦某层是整画布
     不透明的，只有严格的层级顺序是对的，因此不做「底图在最底所以可以短路」的优化。

  两种指定方式并存：**点选一个现成的图层**（位置框由图层拆分免费给出，用户不必自己划）与
  **拖拽划出范围**（要改的不是某个现成图层时用）。两者都归一到声明里的坐标区间后 emit 出去，
  由父组件决定它进哪一句提示词。
-->
<template>
  <div class="flex flex-col gap-3">
    <!-- 画面：合成结果与交互叠层分两张画布——高亮框不能出现在导出的合成图里 -->
    <div
      ref="stageRef"
      class="relative w-full overflow-hidden rounded border border-(--el-border-color) bg-[repeating-conic-gradient(#f3f4f6_0%_25%,#ffffff_0%_50%)] bg-[length:16px_16px]"
      :style="{ aspectRatio: `${size.width} / ${size.height}` }"
    >
      <canvas ref="imageCanvasRef" class="block h-full w-full" />
      <canvas
        ref="overlayCanvasRef"
        class="absolute inset-0 block h-full w-full cursor-crosshair"
        @pointerdown="onPointerDown"
        @pointermove="onPointerMove"
        @pointerup="onPointerUp"
        @pointerleave="onPointerUp"
      />
      <div
        v-if="loading || !ready"
        class="absolute inset-0 flex items-center justify-center bg-(--el-mask-color) px-4 text-center text-sm"
      >
        {{ loading ? t("image_workbench.canvas.loading") : t("image_workbench.canvas.noPicture") }}
      </div>
    </div>

    <!-- 拆分画面的重绘说明（design D1 刻意接受的失效模式：看得见的偏差优于看不见的错位） -->
    <ElAlert
      v-if="isComposition"
      type="info"
      :closable="false"
      show-icon
      :title="t('image_workbench.canvas.redrawNotice')"
    />

    <div class="flex flex-wrap items-center justify-between gap-2">
      <span class="text-xs text-(--el-text-color-secondary)">
        {{ hintText }}
      </span>
      <div class="flex items-center gap-2">
        <ElButton v-if="isComposition" size="small" @click="clearHighlight">
          {{ t("image_workbench.canvas.clearHighlight") }}
        </ElButton>
        <ElButton
          type="primary"
          size="small"
          :loading="exporting"
          :disabled="!ready"
          @click="exportComposition"
        >
          {{ t("image_workbench.canvas.useAsReference") }}
        </ElButton>
      </div>
    </div>

    <!-- 已指定的位置：序号与提示词里的区域占位符一一对应 -->
    <div v-if="selections.length > 0" class="flex flex-wrap gap-2">
      <ElTag v-for="item in selections" :key="item.index" size="small" type="success">
        {{ t("image_workbench.canvas.selectionTag", { index: item.index, bbox: bboxText(item) }) }}
      </ElTag>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted } from "vue";
import { ElMessage } from "element-plus";
import JobAPI, { type JobArtifact, type JobSelectionInput } from "@/api/module_image_workbench/job";
import { absoluteBox, planComposition } from "../composition";
import { staticUrl } from "../../values";

defineOptions({
  name: "ImageWorkbenchEditCanvas",
  inheritAttrs: false,
});

const props = defineProps<{
  /** 图层拆分产物（底图 + 各图层）；有它即「合成画面」，否则显示参考图 */
  artifacts?: JobArtifact[];
  /** 产物所属任务：没有可直接访问地址时经下载接口取图（否则画布拿不到像素） */
  jobId?: number | null;
  /** 参考图路径（画布不来自产物时使用） */
  referencePath?: string | null;
  /** 该模型声明的坐标区间（0..range-1）；画布据此把像素换算成坐标 */
  range: number;
  /** 已指定的选区（只用于呈现序号） */
  selections?: JobSelectionInput[];
}>();

const emit = defineEmits<{
  /** 指定了一个位置；`artifactId` 为空表示用户自己划的框 */
  (e: "pick", payload: { bbox: [number, number, number, number]; artifactId?: number }): void;
  /** 合成图已导出并上传，调用方把它作为本次任务的参考图 */
  (e: "composed", payload: { filePath: string; hasAlpha: boolean }): void;
}>();

const { t } = useI18n();

const stageRef = ref<HTMLDivElement | null>(null);
const imageCanvasRef = ref<HTMLCanvasElement | null>(null);
const overlayCanvasRef = ref<HTMLCanvasElement | null>(null);

const loading = ref(false);
const exporting = ref(false);
const ready = ref(false);
/** 当前高亮的图层（点选或吸附中的那个） */
const highlighted = ref<JobArtifact | null>(null);
/** 拖拽中的橡皮筋（画布像素坐标） */
const dragRect = ref<{ x: number; y: number; w: number; h: number } | null>(null);

/** 选区序号 + 坐标 → 标签文案（`bbox` 在入参里可空，这里统一呈现为文本） */
function bboxText(item: JobSelectionInput) {
  return (item.bbox ?? []).join(", ");
}

const selections = computed(() => props.selections ?? []);

/** 叠放计划（底图 + 按层级升序的图层）：规则在 composition.ts，这里只负责画 */
const plan = computed(() => planComposition(props.artifacts));
const baseArtifact = computed(() => plan.value.base);
const layers = computed(() => plan.value.layers);

const isComposition = computed(() => layers.value.length > 0);

/** 画布尺寸（合成坐标系）：优先底图的实际像素，没有底图时取各图层位置框的并集 */
const size = ref({ width: 1024, height: 1024 });

const hintText = computed(() =>
  isComposition.value ? t("image_workbench.canvas.pickHint") : t("image_workbench.canvas.drawHint")
);

/**
 * 取产物的像素。
 *
 * 优先用可直接访问地址（本地存储节点下后端已给出）；没有时经**下载接口**取回并转成对象
 * URL——画布要读像素，跨域或缺失都会让导出失败，而 blob: 是同源的。
 */
async function loadArtifactImage(artifact: JobArtifact): Promise<HTMLImageElement | null> {
  const src = artifact.image_url ?? (await artifactBlobUrl(artifact));
  if (!src) return null;
  try {
    return await loadImage(src);
  } catch {
    return null;
  }
}

async function artifactBlobUrl(artifact: JobArtifact): Promise<string | null> {
  if (!props.jobId) return null;
  try {
    const response = await JobAPI.downloadArtifact(props.jobId, artifact.id);
    return URL.createObjectURL(response.data as Blob);
  } catch {
    return null;
  }
}

function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.crossOrigin = "anonymous";
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error("image load failed"));
    image.src = src;
  });
}

/**
 * 合成：底图先铺底，各图层按 `z_index` 升序、按其位置框缩放后摆放（design D2）。
 *
 * 这里刻意不做任何「底图一定在最底」的短路——层级顺序就是叠放顺序。
 */
async function compose(): Promise<void> {
  const canvas = imageCanvasRef.value;
  if (!canvas) return;
  loading.value = true;
  ready.value = false;
  try {
    const baseImage = baseArtifact.value ? await loadArtifactImage(baseArtifact.value) : null;
    const referenceImage =
      !baseImage && props.referencePath ? await loadImage(staticUrl(props.referencePath)) : null;
    const picture = baseImage ?? referenceImage;

    const layerImages: {
      image: HTMLImageElement;
      box: [number, number, number, number];
    }[] = [];
    for (const layer of layers.value) {
      const image = await loadArtifactImage(layer.artifact);
      if (image) layerImages.push({ image, box: layer.box });
    }

    // 有底图时以它的实际像素为准；没有底图时用各图层位置框的并集
    const width = picture?.naturalWidth || plan.value.fallbackWidth || 1024;
    const height = picture?.naturalHeight || plan.value.fallbackHeight || 1024;
    size.value = { width, height };

    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.clearRect(0, 0, width, height);
    if (picture) ctx.drawImage(picture, 0, 0, width, height);
    for (const item of layerImages) {
      const [x1, y1, x2, y2] = item.box;
      // 图层是按各自位置框**紧裁**的：先缩放到位置框的宽高，再贴到它的 (x, y)
      ctx.drawImage(item.image, x1, y1, Math.max(1, x2 - x1), Math.max(1, y2 - y1));
    }
    syncOverlaySize();
    drawOverlay();
    // 没有任何画面（既无产物也无参考图）时保持不可用：空白画布上的框落在一个虚构的
    // 坐标系里，提交出去只会得到一张对不上的图
    ready.value = !!(picture || layerImages.length);
  } finally {
    loading.value = false;
  }
}

function syncOverlaySize() {
  const overlay = overlayCanvasRef.value;
  if (!overlay) return;
  overlay.width = size.value.width;
  overlay.height = size.value.height;
}

/** 叠层：高亮框与拖拽中的橡皮筋（都不进导出的合成图） */
function drawOverlay() {
  const overlay = overlayCanvasRef.value;
  const ctx = overlay?.getContext("2d");
  if (!overlay || !ctx) return;
  ctx.clearRect(0, 0, overlay.width, overlay.height);

  const box = highlighted.value ? absoluteBox(highlighted.value) : null;
  if (box) {
    const [x1, y1, x2, y2] = box;
    ctx.save();
    ctx.lineWidth = Math.max(2, overlay.width / 400);
    ctx.strokeStyle = "rgba(64, 158, 255, 1)";
    ctx.fillStyle = "rgba(64, 158, 255, 0.22)";
    ctx.fillRect(x1, y1, x2 - x1, y2 - y1);
    ctx.strokeRect(x1, y1, x2 - x1, y2 - y1);
    ctx.restore();
  }

  if (dragRect.value) {
    const { x, y, w, h } = dragRect.value;
    ctx.save();
    ctx.setLineDash([6, 4]);
    ctx.lineWidth = Math.max(2, overlay.width / 400);
    ctx.strokeStyle = "rgba(230, 162, 60, 1)";
    ctx.strokeRect(x, y, w, h);
    ctx.restore();
  }
}

function clearHighlight() {
  highlighted.value = null;
  drawOverlay();
}

// ── 指针交互：点选图层（命中测试）与拖拽划框 ──

let dragStart: { x: number; y: number } | null = null;

/** 显示坐标 → 合成坐标（画布按容器缩放，必须用 rect 与 canvas 尺寸的比值换算） */
function toCanvasPoint(event: PointerEvent): { x: number; y: number } {
  const overlay = overlayCanvasRef.value;
  if (!overlay) return { x: 0, y: 0 };
  const rect = overlay.getBoundingClientRect();
  return {
    x: ((event.clientX - rect.left) / rect.width) * overlay.width,
    y: ((event.clientY - rect.top) / rect.height) * overlay.height,
  };
}

/** 合成坐标 → 声明里的坐标（0..range-1），越界即夹到边界 */
function toNormalized(x: number, y: number): [number, number] {
  const clamp = (value: number, total: number) =>
    Math.min(props.range - 1, Math.max(0, Math.round((value / total) * props.range)));
  return [clamp(x, size.value.width), clamp(y, size.value.height)];
}

/** 命中最上面那个包含该点的图层（层级高的优先，与视觉上的叠放一致） */
function hitTest(point: { x: number; y: number }): JobArtifact | null {
  for (let index = layers.value.length - 1; index >= 0; index -= 1) {
    const layer = layers.value[index];
    if (!layer) continue;
    const [x1, y1, x2, y2] = layer.box;
    if (point.x >= x1 && point.x <= x2 && point.y >= y1 && point.y <= y2) return layer.artifact;
  }
  return null;
}

function onPointerDown(event: PointerEvent) {
  if (!ready.value) return;
  const point = toCanvasPoint(event);
  dragStart = point;
  dragRect.value = { x: point.x, y: point.y, w: 0, h: 0 };
  overlayCanvasRef.value?.setPointerCapture?.(event.pointerId);
  drawOverlay();
}

function onPointerMove(event: PointerEvent) {
  if (!dragStart) return;
  const point = toCanvasPoint(event);
  dragRect.value = {
    x: Math.min(dragStart.x, point.x),
    y: Math.min(dragStart.y, point.y),
    w: Math.abs(point.x - dragStart.x),
    h: Math.abs(point.y - dragStart.y),
  };
  drawOverlay();
}

/**
 * 松开时判定：几乎没移动即视为**点选**（命中哪个图层就用它的位置框），否则是**划框**。
 *
 * 阈值按画布宽度的 1% 取——比它小的一下点击不该被当成一个退化成线的框（服务端会以
 * 「面积为零」拒掉，用户却不知道自己哪里做错了）。
 */
function onPointerUp(event: PointerEvent) {
  if (!dragStart) return;
  const rect = dragRect.value;
  const start = dragStart;
  dragStart = null;
  dragRect.value = null;
  if (!rect || !start) return;
  overlayCanvasRef.value?.releasePointerCapture?.(event.pointerId);

  const isClick = rect.w < size.value.width / 100 && rect.h < size.value.height / 100;
  if (isClick) {
    const artifact = hitTest(toCanvasPoint(event));
    if (!artifact) {
      highlighted.value = null;
      drawOverlay();
      ElMessage.info(t("image_workbench.canvas.noLayerHit"));
      return;
    }
    highlighted.value = artifact;
    drawOverlay();
    emit("pick", { bbox: normalizedBoxOf(artifact), artifactId: artifact.id });
    return;
  }

  const [x1, y1] = toNormalized(rect.x, rect.y);
  const [x2, y2] = toNormalized(rect.x + rect.w, rect.y + rect.h);
  if (x1 === x2 || y1 === y2) {
    ElMessage.warning(t("image_workbench.canvas.tooSmall"));
    drawOverlay();
    return;
  }
  emit("pick", { bbox: [x1, y1, x2, y2] });
  drawOverlay();
}

/** 图层位置框 → 声明里的坐标（点选时坐标由**服务端**读位置框，这里只用于本地提示） */
function normalizedBoxOf(artifact: JobArtifact): [number, number, number, number] {
  const normalized = artifact.bounding_box?.normalized;
  if (Array.isArray(normalized) && normalized.length === 4) {
    return normalized.map((item) => Number(item)) as [number, number, number, number];
  }
  const box = absoluteBox(artifact);
  if (!box) return [0, 0, 0, 0];
  const [nx1, ny1] = toNormalized(box[0], box[1]);
  const [nx2, ny2] = toNormalized(box[2], box[3]);
  return [nx1, ny1, nx2, ny2];
}

// ── 导出并上传（design D1：走既有参考图上传接口） ─

async function exportComposition() {
  const canvas = imageCanvasRef.value;
  if (!canvas) return;
  exporting.value = true;
  try {
    const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/png"));
    if (!blob) {
      ElMessage.error(t("image_workbench.canvas.exportFailed"));
      return;
    }
    const body = new FormData();
    body.append("file", new File([blob], "canvas.png", { type: "image/png" }));
    const response = await JobAPI.uploadReferenceImage(body);
    const result = response.data.data;
    if (!result?.file_path) {
      ElMessage.error(t("image_workbench.job.referenceUploadFailed"));
      return;
    }
    emit("composed", { filePath: result.file_path, hasAlpha: !!result.has_alpha });
  } catch {
    // 上传失败已由拦截器提示
  } finally {
    exporting.value = false;
  }
}

watch(
  () => [props.artifacts, props.referencePath],
  () => {
    void compose();
  },
  { deep: true }
);

onMounted(() => {
  void compose();
});

defineExpose({ exportComposition });
</script>
