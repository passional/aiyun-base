import { request } from "@utils";

const API_PATH = "/voice_workbench/services";

/** 语音服务分页查询参数 */
export interface ServicePageQuery extends PageQuery {
  name?: string;
  status?: number;
  /** 排序字段 JSON 字符串，如 [{"id":"asc"}] */
  order_by?: string;
}

/** 语音服务列表项（凭证只回显脱敏值与是否已配置） */
export interface ServiceTable extends BaseType {
  name: string;
  status: number;
  remark?: string | null;
  /** 脱敏占位，如 ••••abcd */
  api_key_masked?: string | null;
  has_api_key?: boolean;
}

/** 语音服务创建/编辑表单（api_key 为明文入参；编辑时留空表示沿用原值） */
export interface ServiceForm extends BaseFormType {
  name?: string;
  /** 明文凭证；编辑时留空表示沿用原凭证 */
  api_key?: string | null;
  status?: number;
  remark?: string | null;
}

const ServiceAPI = {
  listService(query: ServicePageQuery) {
    return request<ApiResponse<PageResult<ServiceTable>>>({
      url: `${API_PATH}/list`,
      method: "get",
      params: query,
    });
  },

  /** 下拉用语音服务（不分页） */
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
};

export default ServiceAPI;
