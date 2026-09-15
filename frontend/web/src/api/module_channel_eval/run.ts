import { request } from "@utils";
import { createSSEClient, httpEndpoint, type SSEClient } from "@utils/sse";

const API_PATH = "/channel_eval/runs";

/** 运行状态机：pending → running → success/failed/cancelled（cancelling 为取消请求中） */
export type RunStatus = "pending" | "running" | "cancelling" | "success" | "failed" | "cancelled";

/** 运行分页查询参数 */
export interface RunPageQuery extends PageQuery {
  platform_id?: number;
  status?: RunStatus;
  /** 排序字段 JSON 字符串，如 [{"id":"desc"}] */
  order_by?: string;
}

/** 运行参数快照（创建时固化，执行与展示均以快照为准） */
export interface RunParams {
  request_count?: number;
  warmup?: boolean;
  interval_seconds?: number;
  timeout_seconds?: number;
  prompt?: string;
  anomaly_threshold_seconds?: number;
}

/** 运行列表项 */
export interface RunTable extends BaseType {
  platform_id: number;
  platform_name?: string | null;
  status: RunStatus;
  params?: RunParams | null;
  combo_count: number;
  started_at?: string | null;
  finished_at?: string | null;
  error?: string | null;
}

/** 「渠道 × 模型」组合入参（渠道编号 + 名称快照 + 模型ID） */
export interface RunComboInput {
  /** 渠道编号（基准渠道隐式渠道为 0） */
  channel_id: number;
  channel_name: string;
  model: string;
}

/** 运行创建参数（组合清单 + 参数快照随运行持久化） */
export interface RunCreateBody {
  platform_id: number;
  combos: RunComboInput[];
  request_count?: number;
  warmup?: boolean;
  interval_seconds?: number;
  timeout_seconds?: number;
  prompt?: string;
  anomaly_threshold_seconds?: number;
}

/** 样本（客户端实测 + 配对后台字段 be_*） */
export interface RunSample {
  id?: number | null;
  combo_id: number;
  seq: number;
  is_warmup: boolean;
  request_id?: string | null;
  ttft_ms?: number | null;
  total_ms?: number | null;
  tokens_in?: number | null;
  tokens_out?: number | null;
  tps?: number | null;
  status: string;
  error?: string | null;
  started_at?: string | null;
  be_matched: boolean;
  be_frt_ms?: number | null;
  be_use_time_ms?: number | null;
  be_tokens_in?: number | null;
  be_tokens_out?: number | null;
  be_cost?: number | null;
  be_channel_id?: number | null;
}

/** 组合聚合（剔除热身/异常/失败样本的均值 + 含异常参考均值 + 成功率） */
export interface RunComboAggregate {
  combo_id: number;
  channel_id: number;
  channel_name: string;
  model: string;
  status: string;
  planned: number;
  success: number;
  failed: number;
  anomaly: number;
  error?: string | null;
  executed: number;
  success_rate?: number | null;
  ttft_avg_ms?: number | null;
  total_avg_ms?: number | null;
  tps_avg?: number | null;
  ttft_with_anomaly_avg_ms?: number | null;
  total_with_anomaly_avg_ms?: number | null;
}

/** 运行详情：参数快照 + 组合聚合 + 逐样本明细 */
export interface RunDetail extends RunTable {
  combos: RunComboAggregate[];
  samples: RunSample[];
}

/** 运行进度（Redis 存储 + SSE 推送载荷） */
export interface RunProgress {
  run_id: number;
  status: RunStatus;
  total_combos: number;
  completed_combos: number;
  current_combo?: string | null;
  current_sample: number;
  message?: string | null;
}

const RunAPI = {
  listRun(query: RunPageQuery) {
    return request<ApiResponse<PageResult<RunTable>>>({
      url: `${API_PATH}/list`,
      method: "get",
      params: query,
    });
  },

  detailRun(id: number) {
    return request<ApiResponse<RunDetail>>({
      url: `${API_PATH}/detail/${id}`,
      method: "get",
    });
  },

  createRun(body: RunCreateBody) {
    return request<ApiResponse<RunTable>>({
      url: `${API_PATH}/create`,
      method: "post",
      data: body,
    });
  },

  /** 取消执行中的运行（在当前请求完成后停止，已产生样本保留） */
  cancelRun(id: number) {
    return request<ApiResponse>({
      url: `${API_PATH}/${id}/cancel`,
      method: "post",
    });
  },
};

/** 运行进度 SSE 客户端（自动重连；断线重连以服务端回放的 Redis 进度为准） */
export class RunStream {
  private client: SSEClient | null = null;
  private readonly onProgress: (progress: RunProgress) => void;
  private readonly onStatus?: (connected: boolean) => void;

  constructor(handlers: {
    onProgress: (progress: RunProgress) => void;
    onStatus?: (connected: boolean) => void;
  }) {
    this.onProgress = handlers.onProgress;
    this.onStatus = handlers.onStatus;
  }

  /** 建立连接（幂等：已存在实例则直接返回，重连由客户端内部负责） */
  connect(runId: number) {
    if (this.client) return;
    const url = new URL(
      `/api/v1/channel_eval/runs/${runId}/events`,
      httpEndpoint(import.meta.env.VITE_APP_WS_ENDPOINT)
    );
    this.client = createSSEClient({
      url: url.toString(),
      onEvent: (event, data) => {
        if (event !== "run_progress") return;
        try {
          this.onProgress(JSON.parse(data) as RunProgress);
        } catch {
          /* ignore */
        }
      },
      onStatus: (connected) => this.onStatus?.(connected),
      // 令牌失效：释放实例，视图层下次重连时以新令牌重建
      onFatal: () => {
        this.client = null;
      },
    });
  }

  disconnect() {
    this.client?.disconnect();
    this.client = null;
  }

  get connected() {
    return this.client?.connected ?? false;
  }
}

export default RunAPI;
