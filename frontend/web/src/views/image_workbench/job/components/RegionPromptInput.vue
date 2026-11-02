<!--
  绘图工作台 - 带区域高亮的提示词输入框。

  原生 textarea 不能只给局部文字上色，所以这里用「镜像层」：textarea 的文字设为透明、
  只保留光标与选区，下层一个字体/行高/内边距完全一致的 div 负责显示文字。镜像层只改
  颜色与淡底色，**不改字重、字距或内边距**——任何会改变字形宽度的样式都会让光标位置
  与可见文字错位。输入法组字期间镜像层暂时隐藏、恢复原生文字显示，否则组字内容会变成
  透明文字（Element Plus 在组字结束前不更新 v-model）。

  区域写法由能力声明传入（`placeholderTemplate`，如 `区域{n}`），不在组件里硬编码。
-->
<template>
  <div class="region-prompt-input" :class="{ 'is-disabled': disabled, 'is-composing': composing }">
    <div ref="mirrorRef" class="region-prompt-input__mirror" aria-hidden="true">
      <span
        v-for="(segment, position) in segments"
        :key="position"
        :class="{ 'region-prompt-input__token': segment.index !== null }"
        :style="segmentStyle(segment)"
        >{{ segment.text }}</span
      >
    </div>
    <ElInput
      ref="inputRef"
      :model-value="modelValue"
      type="textarea"
      :rows="rows ?? 4"
      :maxlength="maxlength"
      show-word-limit
      :placeholder="placeholder"
      :disabled="disabled"
      @update:model-value="onUpdate"
    />
  </div>
</template>

<script setup lang="ts">
import { regionColor, regionSoftColor, splitPromptSegments } from "../regions";

defineOptions({
  name: "ImageWorkbenchRegionPromptInput",
});

const props = defineProps<{
  modelValue: string;
  placeholder?: string;
  rows?: number;
  maxlength?: number;
  disabled?: boolean;
  /** 能力声明里的占位形态，如 `区域{n}`；为空时整段不染色 */
  placeholderTemplate?: string | null;
}>();

const emit = defineEmits<{
  (e: "update:modelValue", value: string): void;
}>();

const inputRef = ref<{ textarea?: HTMLTextAreaElement } | null>(null);
const mirrorRef = ref<HTMLDivElement | null>(null);
/** 输入法组字中：此时 v-model 尚未更新，镜像层会落后于原生输入 */
const composing = ref(false);

const textareaEl = computed(() => inputRef.value?.textarea ?? null);
const segments = computed(() =>
  splitPromptSegments(props.modelValue ?? "", props.placeholderTemplate)
);

/** 区域片段：只改颜色和淡底色，绝不改字重/字距/内边距（会与光标错位） */
function segmentStyle(segment: { text: string; index: number | null }) {
  if (segment.index === null) return undefined;
  return {
    color: regionColor(segment.index),
    backgroundColor: regionSoftColor(segment.index),
  };
}

function onUpdate(value: string) {
  emit("update:modelValue", value);
  nextTick(syncMetrics);
}

/**
 * 从 textarea 的**计算样式**复制文字排版参数，而不是在 CSS 里再写一份。
 *
 * 镜像层的字形宽度必须与 textarea 完全一致，否则光标会落在可见文字之外；把 Element Plus
 * 的内边距、字号、行高、换行规则原样抄过来，比手写一组常量更不容易随版本漂移。
 */
function syncTextStyle(textarea: HTMLTextAreaElement, mirror: HTMLDivElement) {
  const style = getComputedStyle(textarea);
  mirror.style.paddingTop = style.paddingTop;
  mirror.style.paddingRight = style.paddingRight;
  mirror.style.paddingBottom = style.paddingBottom;
  mirror.style.paddingLeft = style.paddingLeft;
  mirror.style.fontFamily = style.fontFamily;
  mirror.style.fontSize = style.fontSize;
  mirror.style.fontStyle = style.fontStyle;
  mirror.style.fontWeight = style.fontWeight;
  mirror.style.lineHeight = style.lineHeight;
  mirror.style.letterSpacing = style.letterSpacing;
  mirror.style.wordSpacing = style.wordSpacing;
  mirror.style.whiteSpace = style.whiteSpace;
  mirror.style.overflowWrap = style.overflowWrap;
  mirror.style.wordBreak = style.wordBreak;
  mirror.style.tabSize = style.tabSize;
}

/**
 * 让镜像层与 textarea 的**内容盒**对齐。
 *
 * 宽度取 `clientWidth` 而不是 `inset: 0`：出现纵向滚动条时 textarea 的内容宽度会变窄，
 * 镜像层若仍按容器宽度排版，换行位置就会与光标错位。
 */
function syncMetrics() {
  const textarea = textareaEl.value;
  const mirror = mirrorRef.value;
  if (!textarea || !mirror) return;
  mirror.style.width = `${textarea.clientWidth}px`;
  mirror.style.height = `${textarea.clientHeight}px`;
  mirror.scrollTop = textarea.scrollTop;
  mirror.scrollLeft = textarea.scrollLeft;
}

/** 滚动只需要同步位置；重排参数在挂载与尺寸变化时复制一次即可 */
function syncScroll() {
  const textarea = textareaEl.value;
  const mirror = mirrorRef.value;
  if (!textarea || !mirror) return;
  mirror.scrollTop = textarea.scrollTop;
  mirror.scrollLeft = textarea.scrollLeft;
}

function onCompositionStart() {
  composing.value = true;
}

function onCompositionEnd() {
  composing.value = false;
  nextTick(syncMetrics);
}

let resizeObserver: ResizeObserver | null = null;
let attached: HTMLTextAreaElement | null = null;

function attach() {
  const textarea = textareaEl.value;
  const mirror = mirrorRef.value;
  if (!textarea || !mirror || textarea === attached) return;
  attached = textarea;
  syncTextStyle(textarea, mirror);
  textarea.addEventListener("scroll", syncScroll, { passive: true });
  textarea.addEventListener("compositionstart", onCompositionStart);
  textarea.addEventListener("compositionend", onCompositionEnd);
  if (typeof ResizeObserver !== "undefined") {
    resizeObserver = new ResizeObserver(() => {
      syncTextStyle(textarea, mirror);
      syncMetrics();
    });
    resizeObserver.observe(textarea);
  }
  syncMetrics();
}

function detach() {
  if (!attached) return;
  attached.removeEventListener("scroll", syncScroll);
  attached.removeEventListener("compositionstart", onCompositionStart);
  attached.removeEventListener("compositionend", onCompositionEnd);
  attached = null;
  resizeObserver?.disconnect();
  resizeObserver = null;
}

defineExpose({ textarea: textareaEl });

watch(
  () => [props.modelValue, props.rows],
  () => nextTick(syncMetrics)
);

onMounted(() => nextTick(attach));
onBeforeUnmount(detach);
</script>

<style scoped lang="scss">
.region-prompt-input {
  position: relative;
  width: 100%;

  &__mirror {
    position: absolute;
    top: 0;
    left: 0;
    z-index: 0;
    box-sizing: border-box;
    overflow: hidden;
    color: var(--el-text-color-regular);
    white-space: pre-wrap;
    pointer-events: none;
    // textarea 的背景被设为透明，镜像层补回输入框底色（含深色模式）
    background-color: var(--el-fill-color-blank);
    border-radius: var(--el-border-radius-base);
  }

  &__token {
    border-radius: var(--el-border-radius-small, 2px);
    -webkit-box-decoration-break: clone;
    box-decoration-break: clone;
  }

  :deep(.el-textarea) {
    position: relative;
    z-index: 1;
  }

  :deep(.el-textarea__inner) {
    color: transparent;
    caret-color: var(--el-text-color-regular);
    background-color: transparent;
  }

  // 选区底色用半透明：文字本身是透明的，不透明底色会把下层的彩色文字盖住
  :deep(.el-textarea__inner::selection) {
    background-color: color-mix(in srgb, var(--el-color-primary) 28%, transparent);
  }

  // 输入法组字期间恢复原生文字：Element Plus 在 compositionend 前不更新 v-model，
  // 镜像层拿不到组字内容，透明文字会让用户看不见正在打的字
  &.is-composing {
    .region-prompt-input__mirror {
      display: none;
    }

    :deep(.el-textarea__inner) {
      color: var(--el-text-color-regular);
      // 镜像层被隐藏，输入框底色改由 textarea 自己补上
      background-color: var(--el-fill-color-blank);
    }
  }

  // 禁用时 ElInput 会铺不透明底色，镜像层被盖住；恢复原生文字即可
  &.is-disabled {
    .region-prompt-input__mirror {
      display: none;
    }

    :deep(.el-textarea.is-disabled .el-textarea__inner) {
      color: var(--el-disabled-text-color);
    }
  }
}
</style>
