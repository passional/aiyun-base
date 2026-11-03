import { request } from "@utils";

const API_PATH = "/voice_workbench/models";

/** 语音识别模型分页查询参数 */
export interface ModelPageQuery extends PageQuery {
  service_id?: number;
  display_name?: string;
  model_name?: string;
  status?: number;
  /** 排序字段 JSON 字符串，如 [{"id":"asc"}] */
  order_by?: string;
}

/** 语音识别模型列表项 */
export interface ModelTable extends BaseType {
  service_id: number;
  service_name?: string | null;
  display_name: string;
  /** 上游请求体里的 model_name，原样存取 */
  model_name: string;
  /** 上游请求头 X-Api-Resource-Id */
  resource_id: string;
  /** 完整 WebSocket 端点 */
  endpoint_url: string;
  /** 单文件大小上限（MB） */
  max_file_mb: number;
  /** 单文件音频时长上限（秒） */
  max_duration_sec: number;
  status: number;
  remark?: string | null;
}

/** 语音识别模型创建/编辑表单 */
export interface ModelForm extends BaseFormType {
  service_id?: number;
  display_name?: string;
  model_name?: string;
  resource_id?: string;
  endpoint_url?: string;
  max_file_mb?: number;
  max_duration_sec?: number;
  status?: number;
  remark?: string | null;
}

const ModelAPI = {
  listModel(query: ModelPageQuery) {
    return request<ApiResponse<PageResult<ModelTable>>>({
      url: `${API_PATH}/list`,
      method: "get",
      params: query,
    });
  },

  /** 下拉用语音识别模型（不分页） */
  modelOptions() {
    return request<ApiResponse<ModelTable[]>>({
      url: `${API_PATH}/optionselect`,
      method: "get",
    });
  },

  detailModel(id: number) {
    return request<ApiResponse<ModelTable>>({
      url: `${API_PATH}/detail/${id}`,
      method: "get",
    });
  },

  createModel(body: ModelForm) {
    return request<ApiResponse<ModelTable>>({
      url: `${API_PATH}/create`,
      method: "post",
      data: body,
    });
  },

  updateModel(id: number, body: ModelForm) {
    return request<ApiResponse<ModelTable>>({
      url: `${API_PATH}/update/${id}`,
      method: "put",
      data: body,
    });
  },

  deleteModel(body: number[]) {
    return request<ApiResponse>({
      url: `${API_PATH}/delete`,
      method: "delete",
      data: body,
    });
  },
};

export default ModelAPI;
