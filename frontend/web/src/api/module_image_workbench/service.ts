import { request } from "@utils";

const API_PATH = "/image_workbench/services";

/**
 * 接入方式：ark=火山方舟（参考图随提示词置于同一请求体），
 * openai=OpenAI 兼容（参考图走独立的图片编辑端点，multipart 表单）
 */
export type ServiceDialect = "ark" | "openai";

/** 绘图服务分页查询参数 */
export interface ServicePageQuery extends PageQuery {
  name?: string;
  dialect?: ServiceDialect;
  status?: number;
  /** 排序字段 JSON 字符串，如 [{"id":"desc"}] */
  order_by?: string;
}

/** 绘图服务列表项（凭证仅脱敏回显，不回显明文/密文） */
export interface ServiceTable extends BaseType {
  name: string;
  dialect: ServiceDialect;
  /** 后端给出的接入方式中文名（如「火山方舟」） */
  dialect_label?: string | null;
  /** 完整 API 前缀，含版本段（如 /v1、/api/plan/v3） */
  base_url: string;
  status: number;
  remark?: string | null;
  /** 脱敏占位，如 ••••abcd */
  api_key_masked?: string | null;
  /** 是否已配置凭证（未配置时其下模型不会出现在工作台） */
  has_api_key?: boolean;
}

/** 绘图服务创建/编辑表单（凭证为明文入参；编辑时留空表示保留原值） */
export interface ServiceForm extends BaseFormType {
  name?: string;
  dialect?: ServiceDialect;
  base_url?: string;
  api_key?: string | null;
  status?: number;
  remark?: string | null;
}

/** 连通性测试入参：已保存的服务只需传 service_id，凭证与地址回退库内值 */
export interface TestConnectionPayload {
  service_id?: number;
  dialect?: ServiceDialect;
  base_url?: string;
  api_key?: string;
  model_id?: string;
}

export interface TestConnectionResult {
  success: boolean;
  message: string;
  dialect?: string | null;
  base_url?: string | null;
  /** 上游返回的模型标识，供管理员核对 */
  model_ids: string[];
  model_found?: boolean | null;
}

const ServiceAPI = {
  listService(query: ServicePageQuery) {
    return request<ApiResponse<PageResult<ServiceTable>>>({
      url: `${API_PATH}/list`,
      method: "get",
      params: query,
    });
  },

  /** 获取全部绘图服务（下拉用，不含分页） */
  serviceOptions() {
    return request<ApiResponse<ServiceTable[]>>({
      url: `${API_PATH}/optionselect`,
      method: "get",
    });
  },

  detailService(id: number) {
    return request<ApiResponse<ServiceTable>>({
      url: `${API_PATH}/detail/${id}`,
      method: "get",
    });
  },

  createService(body: ServiceForm) {
    return request<ApiResponse<ServiceTable>>({
      url: `${API_PATH}/create`,
      method: "post",
      data: body,
    });
  },

  updateService(id: number, body: ServiceForm) {
    return request<ApiResponse<ServiceTable>>({
      url: `${API_PATH}/update/${id}`,
      method: "put",
      data: body,
    });
  },

  deleteService(body: number[]) {
    return request<ApiResponse>({
      url: `${API_PATH}/delete`,
      method: "delete",
      data: body,
    });
  },

  /** 测试连接（用弹窗所填凭证，不依赖已保存服务） */
  testConnection(body: TestConnectionPayload) {
    return request<ApiResponse<TestConnectionResult>>({
      url: `${API_PATH}/test-connection`,
      method: "post",
      data: body,
    });
  },

  /** 测试已保存服务的连接（凭证回退库内，无需重填） */
  testSavedConnection(id: number, body?: TestConnectionPayload) {
    return request<ApiResponse<TestConnectionResult>>({
      url: `${API_PATH}/${id}/test-connection`,
      method: "post",
      data: body ?? {},
    });
  },
};

export default ServiceAPI;
