import { request, type ExtendedRequestConfig } from "@utils";
import { createSSEClient, httpEndpoint, type SSEClient } from "@utils/sse";
import type { ModelCapability } from "./model";

/** 参考图上传响应：在原上传结果上补一个透明通道标志（上传时即判定，design D9） */
export interface ReferenceUploadResult extends UploadFilePath {
  /** 参考图是否带透明通道；供「图片透明通道」等依赖本次输入的参数当场决定可用性 */
  has_alpha: boolean;
}

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

/**
 * 任务参数快照：**完整且已解析的参数集**，等于实际发给上游的参数集（含服务端补齐的默认值）。
 *
 * 键为所选模型能力声明里的参数键（点号表示嵌套，如 `optimize_prompt_options.mode`），
 * 因此是一个开放字典——新增参数不需要改这里。
 */
export type JobParams = Record<string, unknown>;

/** 单张产物（底图 + 图层）；`is_base` 即 z_index 为空的那一张 */
export interface JobArtifact {
  id: number;
  /** 层级；底图为 null */
  z_index?: number | null;
  is_base?: boolean;
  /** 图层名称 */
  name?: string | null;
  /** 图层描述 */
  description?: string | null;
  /** 图层在底图中所占的位置（绝对与归一化两种表示） */
  bounding_box?: Record<string, unknown> | null;
  file_path?: string | null;
  /** 产物可直接访问地址；非本地存储节点或未配置存储时为空，此时走下载接口 */
  image_url?: string | null;
  image_size?: string | null;
  output_format?: string | null;
}

/**
 * 提交一个选区：序号 + 位置来源**二选一**。
 *
 * `artifact_id` = 点选一个现成的图层（坐标由服务端读该产物的位置框，用户不必自己划）；
 * `bbox` = 用户自己划出的范围 `[左上x, 左上y, 右下x, 右下y]`（归一化坐标）。
 */
export interface JobSelectionInput {
  /** 与提示词里的区域占位符序号对应（`区域1` → 1），不小于 1 且互不重复 */
  index: number;
  /** 点选图层时的来源产物ID */
  artifact_id?: number;
  /** 自行划框时的归一化坐标 */
  bbox?: [number, number, number, number];
}

/** 选区快照（任务详情返回）：坐标**一定有**，`artifact_id` 记下它取自哪一层 */
export interface JobSelection {
  index: number;
  bbox: number[];
  /** 点选图层时的来源产物ID；自行划框时为空 */
  artifact_id?: number | null;
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
  /** 用户写的原话（含区域占位符） */
  prompt: string;
  /** 实际发给上游的那段文本（含坐标标记）；与 prompt 相同时界面只呈现一份 */
  prompt_final: string;
  /** 本任务的选区快照（含来源产物标识）；未使用交互编辑时为空 */
  selections?: JobSelection[];
  params?: JobParams | null;
  status: JobStatus;
  /** 后端给出的状态中文名 */
  status_label?: string | null;
  error?: string | null;
  usage?: JobUsage | null;
  elapsed_ms?: number | null;
  /** 参考图在本系统内的相对路径数组 */
  reference_images?: string[];
  /** 产物逐张呈现（底图在前，图层按层级升序）；列表与详情共用 */
  artifacts?: JobArtifact[];
  /** 列表缩略图地址（取底图）；无可直接访问地址时为空 */
  thumbnail_url?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  created_id?: number | null;
}

/**
 * 提交绘图任务（开放参数字典，design D5）。
 *
 * `params` 只装**用户改动过**的键；未提交的参数由服务端按清单补默认值——两边因此
 * 不会对「默认值到底是几」产生分歧（`watermark` 上游默认加水印，漏传就是相反的成品）。
 */
export interface JobForm {
  /** 绘图模型主键（注意不是 model_id） */
  model_pk?: number;
  prompt?: string;
  params?: Record<string, unknown>;
  reference_images?: string[];
  /**
   * 选区：序号与提示词里的区域占位符对应。
   *
   * 服务端把占位符渲染成坐标标记；提示词引用了选区却没有坐标、或有选区却没被提示词引用时
   * **提交会被拒绝**（坐标绝不静默丢弃）。
   */
  selections?: JobSelectionInput[];
  /**
   * 画面来源：构成本次参考图的那次生成（画布合成时用）。
   *
   * 提交的是**任务标识**而不是产物清单——服务端自己展开成该次生成的全部产物并逐一记为
   * 来源，「记全」因此由构造保证。画布不来自产物（例如直接上传的参考图）时缺省。
   */
  composition_source_job_id?: number;
}

/** 服务端 SSE 推送的进度载荷 */
export interface JobProgress {
  job_id: number;
  owner_id?: number | null;
  status?: JobStatus;
  message?: string | null;
  error?: string | null;
  /** 本次任务的产物张数（终态时给出） */
  artifact_count?: number | null;
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

  /**
   * 删除绘图任务（软删记录 + 真删产物）。
   *
   * `showErrorMessage: false` 用于**引用保护**那条路径：被拒时答复里带着「哪些任务引用了
   * 它」的标识，调用方要把它当成一条内容完整呈现出来，而不是一闪而过的提示条。
   */
  deleteJob(body: number[], showErrorMessage = true) {
    // 先落到 ExtendedRequestConfig 再交给 request：`showErrorMessage` 是本仓库对 axios 配置
    // 的扩展项，直接写对象字面量会被 AxiosRequestConfig 的多余属性检查拦下
    const config: ExtendedRequestConfig = {
      url: `${API_PATH}/delete`,
      method: "delete",
      data: body,
      showErrorMessage,
    };
    return request<ApiResponse>(config);
  },

  /**
   * 下载**某一张**产物：后端返回二进制附件，文件名形如 {job_uuid}-base.png。
   *
   * 任务级下载端点已随多产物一并移除：一次任务可以产出底图 + 多个图层，
   * 「任务 → 一个文件」的假设不再成立。
   */
  downloadArtifact(id: number, artifactId: number) {
    return request<Blob>({
      url: `${API_PATH}/${id}/artifacts/${artifactId}/download`,
      method: "get",
      responseType: "blob",
    });
  },

  /**
   * 参考图上传：走绘图工作台专用入口，后端校验真实图片内容并落到 static/upload 下。
   * 提交时用的是响应里的 `file_path`（存储节点内的相对路径），
   * **不是** `file_url`——后端会校验路径必须落在上传目录内。
   *
   * 响应里的 `has_alpha` 由头部解析在上传时判出，供表单当场置灰「图片透明通道」。
   */
  uploadReferenceImage(body: FormData) {
    return request<ApiResponse<ReferenceUploadResult>>({
      url: `${API_PATH}/reference-images/upload`,
      method: "post",
      data: body,
      headers: { "Content-Type": "multipart/form-data" },
    });
  },
};

export default JobAPI;
