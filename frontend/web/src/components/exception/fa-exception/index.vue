<template>
  <div class="exception-page">
    <div class="exception-inner">
      <div class="exception-brand">
        <FaLogo size="30" />
        <span>FastapiAdmin</span>
      </div>

      <h1 class="exception-code">{{ data.title }}</h1>
      <p class="exception-desc">{{ data.desc }}</p>

      <div class="exception-actions">
        <ElButton type="primary" size="large" v-ripple @click="backHome">
          {{ data.btnText }}
        </ElButton>
        <ElButton size="large" @click="backPrev">返回上一页</ElButton>
      </div>
    </div>
  </div>
</template>

<style scoped lang="scss">
.exception-page {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  padding: 24px;
  overflow: hidden;
  color: var(--fa-text-1, var(--el-text-color-primary));
  background: var(--fa-canvas, var(--default-bg-color));
}

.exception-page::before {
  position: absolute;
  top: -180px;
  right: -140px;
  width: 560px;
  height: 560px;
  content: "";
  background: radial-gradient(
    circle,
    color-mix(in srgb, var(--fa-brand-blue, #4f6bff) 14%, transparent) 0%,
    transparent 70%
  );
  border-radius: 50%;
}

.exception-inner {
  position: relative;
  z-index: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  width: 100%;
  max-width: 520px;
  text-align: center;
}

.exception-brand {
  display: inline-flex;
  gap: 8px;
  align-items: center;
  font-size: 14px;
  font-weight: 600;
  color: var(--fa-text-2, var(--el-text-color-regular));
}

.exception-code {
  margin: 28px 0 0;
  font-size: 96px;
  font-weight: 700;
  line-height: 1;
  letter-spacing: -0.05em;
  color: transparent;
  background: linear-gradient(
    135deg,
    var(--fa-brand-blue, #4f6bff) 0%,
    var(--fa-brand-violet, #6d5bff) 100%
  );
  background-clip: text;
  -webkit-background-clip: text;
}

.exception-desc {
  margin: 10px 0 0;
  font-size: 15px;
  line-height: 1.6;
  color: var(--fa-text-2, var(--el-text-color-regular));
}

.exception-actions {
  display: flex;
  gap: 12px;
  margin-top: 24px;
}

@media (width <= 480px) {
  .exception-code {
    font-size: 72px;
  }

  .exception-actions {
    flex-wrap: wrap;
    justify-content: center;
  }
}
</style>

<script setup lang="ts">
import { useCommon } from "@/hooks/core/useCommon";
import { useUserStore } from "@stores";

const router = useRouter();
const userStore = useUserStore();

interface ExceptionData {
  /** 标题 */
  title: string;
  /** 描述 */
  desc: string;
  /** 按钮文本 */
  btnText: string;
  /** 图片地址 */
  imgUrl: string;
}

withDefaults(defineProps<{ data: ExceptionData }>(), {});

const { homePath } = useCommon();

function backHome() {
  const targetHomePath = homePath.value || "/";
  if (!userStore.isLogin) {
    router.push({ name: "Login", query: { redirect: targetHomePath } });
    return;
  }
  router.push(targetHomePath);
}

function backPrev() {
  if (window.history.length > 1) router.back();
  else backHome();
}
</script>
