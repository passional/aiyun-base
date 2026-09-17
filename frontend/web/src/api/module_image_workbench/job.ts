import { request } from "@utils";
import { createSSEClient, httpEndpoint, type SSEClient } from "@utils/sse";
import type { ModelCapability } from "./model";

const API_PATH = "/image_workbench/jobs";

export type JobStatus = "pending" | "running" | "success" | "failed" | "cancelled";

/** 工作台可选模型（仅列启用且所属服务已配凭证的模型，含能力声明） */
export interface JobModelOption {
  /** 绘图模型主键，提交时作为 model_pk 传回 */
  id: number;
  service_id: number;
  service_name: string;
  /** 上游真实模型标识 */
  model_id: string;
  display_name: string;
  capability: ModelCapability;
}

/** 任务参数快照 */
export interface JobParams {
  size?: string | null;
  output_format?: string | null;
  watermark?: boolean | null;
  /** 参考图在本系统内的相对路径数组 */
  reference_images?: string[] | null;
}

/** 用量明细：上游 usage 原样保留 + 生成张数 */
export interface JobUsage {
  images?: number;
  upstream?: Record<string, unknown> | null;
}

/** 绘图任务分页查询参数 */
export interface JobPageQuery extends PageQuery {
  prompt?: string;
  model_id?: string;
  status?: JobStatus;
  service_id?: number;
  /** 排序字段 JSON 字符串，如 [{"id":"desc"}] */
  order_by?: string;
}

/** 绘图任务列表项 / 详情 */
export interface JobTable extends BaseType {
  service_id: number;
  service_name?: string | null;
  model_pk: number;
  model_id: string;
  model_display_name?: string | null;
  prompt: string;
  params?: JobParams | null;
  status: JobStatus;
  /** 后端给出的状态中文名 */
  status_label?: string | null;
  error?: string | null;
  usage?: JobUsage | null;
  elapsed_ms?: number | null;
  /** 产物在存储节点中的相对路径（成功时非空） */
  image_path?: string | null;
  /** 产物的直接访问地址；非本地存储节点或未配置存储时为空，此时走下载接口 */
  image_url?: string | null;
  image_size?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  created_id?: number | null;
}

/** 提交绘图任务（水印不下发，由后端取能力声明的默认值） */
export interface JobForm {
  /** 绘图模型主键（注意不是 model_id） */
  model_pk?: number;
  prompt?: string;
  size?: string | null;
  output_format?: string | null;
  reference_images?: string[];
}

/** 服务端 SSE 推送的进度载荷 */
export interface JobProgress {
  job_id: number;
  owner_id?: number | null;
  status?: JobStatus;
  message?: string | null;
  error?: string | null;
  image_path?: string | null;
  elapsed_ms?: number | null;
}

/** 服务端推送消息（事件名固定为 job_progress） */
export type JobPushMessage = { type: "job_progress"; data: JobProgress };

/**
 * 绘图任务进度流（令牌走 Authorization 头，自动重连）。
 *
 * 连接按任务维度保持：后端在建流时会先回放 Redis 中的进度快照，
 * 因此断线重连后不会停留在「进行中」。连接生命周期由视图层控制。
 */
export class JobStream {
  private client: SSEClient | null = null;
  private jobId: number | null = null;
  private handlers: {
    onMessage: (msg: JobPushMessage) => void;
    onStatus: (connected: boolean) => void;
  };

  constructor(handlers: {
    onMessage: (msg: JobPushMessage) => void;
    onStatus: (connected: boolean) => void;
  }) {
    this.handlers = handlers;
  }

  /** 建立连接（幂等：同一任务且已有连接时直接返回，重连由客户端内部负责） */
  connect(jobId: number) {
    if (this.client && this.jobId === jobId) return;
    this.disconnect();
    this.jobId = jobId;
    const url = new URL(
      `/api/v1/image_workbench/jobs/${jobId}/events`,
      httpEndpoint(import.meta.env.VITE_APP_WS_ENDPOINT)
    );
    this.client = createSSEClient({
      url: url.toString(),
      onEvent: (event, data) => {
        if (event !== "job_progress") return;
        try {
          this.handlers.onMessage(JSON.parse(data));
        } catch {
          /* ignore */
        }
      },
      onStatus: (connected) => this.handlers.onStatus(connected),
      // 令牌失效：释放实例，视图层下次 sync 时以新令牌重建
      onFatal: () => {
        this.client = null;
        this.jobId = null;
      },
    });
  }

  disconnect() {
    this.client?.disconnect();
    this.client = null;
    this.jobId = null;
  }

  get connected() {
    return this.client?.connected ?? false;
  }
}

const JobAPI = {
  /** 工作台可选模型（含能力声明） */
  jobModelOptions() {
    return request<ApiResponse<JobModelOption[]>>({
      url: `${API_PATH}/models/optionselect`,
      method: "get",
    });
  },

  listJob(query: JobPageQuery) {
    return request<ApiResponse<PageResult<JobTable>>>({
      url: `${API_PATH}/list`,
      method: "get",
      params: query,
    });
  },

  detailJob(id: number) {
    return request<ApiResponse<JobTable>>({
      url: `${API_PATH}/detail/${id}`,
      method: "get",
    });
  },

  createJob(body: JobForm) {
    return request<ApiResponse<JobTable>>({
      url: `${API_PATH}/create`,
      method: "post",
      data: body,
    });
  },

  cancelJob(id: number) {
    return request<ApiResponse>({
      url: `${API_PATH}/${id}/cancel`,
      method: "post",
    });
  },

  /** 以原参数新建任务（原失败任务保留） */
  retryJob(id: number) {
    return request<ApiResponse<JobTable>>({
      url: `${API_PATH}/${id}/retry`,
      method: "post",
    });
  },

  deleteJob(body: number[]) {
    return request<ApiResponse>({
      url: `${API_PATH}/delete`,
      method: "delete",
      data: body,
    });
  },

  /** 下载产物：后端返回二进制附件，文件名形如 {uuid}.{ext} */
  downloadJob(id: number) {
    return request<Blob>({
      url: `${API_PATH}/${id}/download`,
      method: "get",
      responseType: "blob",
    });
  },

  /**
   * 参考图上传：走绘图工作台专用入口，后端校验真实图片内容并落到 static/upload 下。
   * 提交时用的是响应里的 `file_path`（存储节点内的相对路径），
   * **不是** `file_url`——后端会校验路径必须落在上传目录内。
   */
  uploadReferenceImage(body: FormData) {
    return request<ApiResponse<UploadFilePath>>({
      url: `${API_PATH}/reference-images/upload`,
      method: "post",
      data: body,
      headers: { "Content-Type": "multipart/form-data" },
    });
  },
};

export default JobAPI;
