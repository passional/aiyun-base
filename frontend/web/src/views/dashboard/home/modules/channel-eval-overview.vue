<template>
  <!-- 无渠道评估查询权限时整体不呈现，也不发起请求（避免 403） -->
  <div v-if="authorized" class="fa-card p-5">
    <div class="fa-card-header">
      <div class="title">
        <h4>渠道评估概览</h4>
        <p>数据取自平台管理与测试运行的既有接口</p>
      </div>
      <ElButton v-if="platformTotal" link type="primary" @click="goPlatform">
        前往平台管理
      </ElButton>
    </div>

    <ElSkeleton v-if="loading" :rows="3" animated />

    <!-- 加载失败：呈现可见失败状态，不显示任何数值 -->
    <ElEmpty v-else-if="failed" description="概览数据加载失败">
      <ElButton type="primary" @click="load">重新加载</ElButton>
    </ElEmpty>

    <!-- 尚未登记平台：空状态并引导前往平台管理 -->
    <ElEmpty v-else-if="platformTotal === 0" description="尚未登记任何平台，先去平台管理添加">
      <ElButton type="primary" @click="goPlatform">前往平台管理</ElButton>
    </ElEmpty>

    <template v-else>
      <ElRow :gutter="16">
        <ElCol :xs="24" :sm="12" :md="8">
          <FaStatsCard
            icon="ri:server-line"
            iconStyle="bg-theme"
            boxStyle="bg-theme/10!"
            title="平台总数"
            :count="platformTotal ?? 0"
            description="已登记的 AI 网关平台"
            :showArrow="false"
          />
        </ElCol>
        <ElCol :xs="24" :sm="12" :md="8">
          <FaStatsCard
            icon="ri:play-circle-line"
            iconStyle="bg-success"
            boxStyle="bg-success/10!"
            title="测试运行总数"
            :count="runTotal ?? 0"
            description="累计创建的评估运行"
            :showArrow="false"
          />
        </ElCol>
      </ElRow>

      <div class="mt-5">
        <p class="mb-1 text-sm text-g-600">最近测试运行</p>
        <ElScrollbar max-height="240px">
          <div
            v-for="run in recentRuns"
            :key="run.id"
            class="flex items-center justify-between py-2.5 text-sm border-b border-g-300 last:border-b-0"
          >
            <div class="flex items-center gap-2 min-w-0">
              <span class="text-g-600">#{{ run.id }}</span>
              <span class="truncate">{{ run.platform_name || "—" }}</span>
            </div>
            <div class="flex items-center gap-3 shrink-0">
              <ElTag :type="runStatusTagType(run.status)" size="small">
                {{ runStatusText(run.status) }}
              </ElTag>
              <span class="text-xs text-g-500">{{
                run.started_at || run.created_time || "—"
              }}</span>
            </div>
          </div>
          <ElEmpty v-if="!recentRuns.length" description="暂无测试运行" :image-size="60" />
        </ElScrollbar>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from "vue";
import { useRouter } from "vue-router";
import PlatformAPI from "@/api/module_channel_eval/platform";
import RunAPI, { type RunTable } from "@/api/module_channel_eval/run";
import { checkPerm } from "@/utils/checkPerm";
import { runStatusTagType, runStatusText } from "@/views/channel_eval/run/status";

defineOptions({ name: "ChannelEvalOverview" });

/** 首页只呈现「最近若干次运行」，明细去测试运行页 */
const RECENT_RUN_LIMIT = 5;

const router = useRouter();

const authorized = computed(
  () => checkPerm("channel_eval:platform:query") && checkPerm("channel_eval:run:query")
);

const loading = ref(true);
const failed = ref(false);
const platformTotal = ref<number | null>(null);
const runTotal = ref<number | null>(null);
const recentRuns = ref<RunTable[]>([]);

async function load() {
  if (!authorized.value) return;
  loading.value = true;
  failed.value = false;
  try {
    const [platformRes, runRes] = await Promise.all([
      PlatformAPI.platformOptions(),
      // 必须显式传 desc：不传时后端 order_by 默认为 id 升序
      RunAPI.listRun({ page_no: 1, page_size: RECENT_RUN_LIMIT, order_by: '[{"id":"desc"}]' }),
    ]);

    platformTotal.value = platformRes.data.data?.length ?? 0;

    const page = runRes.data.data;
    runTotal.value = page?.total ?? 0;
    recentRuns.value = page?.items ?? [];
  } catch {
    // 失败必须可见：不呈现任何数值
    failed.value = true;
    platformTotal.value = null;
    runTotal.value = null;
    recentRuns.value = [];
  } finally {
    loading.value = false;
  }
}

const goPlatform = () => {
  router.push("/channel-eval/platform").catch(() => {});
};

onMounted(load);
</script>
