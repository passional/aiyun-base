import { request, type ExtendedRequestConfig } from "@utils";

const API_PATH = "/voice_workbench/jobs";

export type JobStatus = "running" | "success" | "failed";

/** 工作台可选模型（仅列模型启用、服务启用且服务已配置凭证的模型） */
export interface JobModelOption {
  /** 语音识别模型主键，提交时作为 model_id 传回 */
  id: number;
  service_id: number;
  service_name: string;
  display_name: string;
  model_name: string;
  max_file_mb: number;
  max_duration_sec: number;
}

/** 转写分段，时间单位为毫秒 */
export interface TranscriptionSegment {
  start_time: number;
  end_time: number;
  text: string;
}

/** 转写任务分页查询参数 */
export interface JobPageQuery extends PageQuery {
  original_filename?: string;
  status?: JobStatus;
  model_pk?: number;
  /** 排序字段 JSON 字符串，如 [{"id":"desc"}] */
  order_by?: string;
}

/** 转写任务列表项 / 详情 */
export interface JobTable extends BaseType {
  service_id: number;
  service_name?: string | null;
  model_pk: number;
  /** 提交时的上游模型标识快照 */
  model_name: string;
  model_display_name?: string | null;
  original_filename: string;
  /** 转码后上传的 WAV 字节数 */
  file_size: number;
  duration_sec: number;
  text?: string | null;
  segments?: TranscriptionSegment[] | null;
  status: JobStatus;
  /** 后端给出的状态中文名；界面按当前语言自行映射 */
  status_label?: string | null;
  error?: string | null;
  elapsed_ms?: number | null;
  usage?: Record<string, unknown> | null;
  started_at?: string | null;
  finished_at?: string | null;
  created_id?: number | null;
}

/** 提交转写：file 为浏览器转码后的 WAV，filename 保留原始音频名 */
function transcribeJob(audio: Blob, filename: string, modelId: number) {
  const body = new FormData();
  body.append("file", audio, filename);
  body.append("model_id", String(modelId));
  const config: ExtendedRequestConfig = {
    url: `${API_PATH}/transcribe`,
    method: "post",
    data: body,
    // 同步识别可能接近网关上限（默认 300s），不能沿用全局 15s
    timeout: 300000,
    // 批量转写逐行呈现成败，避免每个文件都弹一条全局提示
    showSuccessMessage: false,
    showErrorMessage: false,
    headers: { "Content-Type": "multipart/form-data" },
  };
  return request<ApiResponse<JobTable>>(config);
}

const JobAPI = {
  /** 工作台可选模型 */
  jobModelOptions() {
    return request<ApiResponse<JobModelOption[]>>({
      url: `${API_PATH}/models/optionselect`,
      method: "get",
    });
  },

  transcribeJob,

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

  /** 软删除转写任务（单条与批量共用） */
  deleteJob(body: number[]) {
    return request<ApiResponse>({
      url: `${API_PATH}/delete`,
      method: "delete",
      data: body,
    });
  },
};

export default JobAPI;
