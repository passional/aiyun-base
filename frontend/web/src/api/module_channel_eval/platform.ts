import { request } from "@utils";

const API_PATH = "/channel_eval/platforms";

/** 平台类型：admin=new-api 管理型（可拉渠道目录/管理日志），user=仅用户级（基准渠道） */
export type PlatformType = "admin" | "user";

/** 平台分页查询参数 */
export interface PlatformPageQuery extends PageQuery {
  name?: string;
  type?: PlatformType;
  is_baseline?: boolean;
  status?: number;
  /** 排序字段 JSON 字符串，如 [{"id":"desc"}] */
  order_by?: string;
}

/** 平台列表项（凭证仅脱敏回显，不回显明文/密文） */
export interface PlatformTable extends BaseType {
  name: string;
  type: PlatformType;
  base_url: string;
  admin_user_id?: string | null;
  is_baseline: boolean;
  status: number;
  remark?: string | null;
  admin_key_masked?: string | null;
  test_key_masked?: string | null;
}

/** 平台创建/编辑表单（凭证为明文入参；编辑时留空表示保留原值） */
export interface PlatformForm extends BaseFormType {
  name?: string;
  type?: PlatformType;
  base_url?: string;
  admin_key?: string | null;
  admin_user_id?: string | null;
  test_key?: string | null;
  is_baseline?: boolean;
  status?: number;
  remark?: string | null;
}

/** 测试连接入参（编辑态凭证/地址留空时回退库内已存值） */
export interface TestConnectionPayload {
  type?: PlatformType;
  base_url?: string;
  admin_key?: string;
  /** 管理用户ID(New-Api-User请求头)；编辑态留空回退库内值 */
  admin_user_id?: string;
  test_key?: string;
  platform_id?: number;
}

/** 测试连接结果 */
export interface TestConnectionResult {
  success: boolean;
  message: string;
}

/** 渠道目录条目（实时拉取，不持久化） */
export interface ChannelCatalogItem {
  channel_id: number;
  name: string;
  /** new-api 渠道状态(1:启用 2:手动停用 3:自动停用) */
  status: number;
  enabled: boolean;
  groups: string[];
  priority: number;
  weight: number;
  models: string[];
  /** 可达性提示：存在同分组同模型的高优先级渠道（备用渠道） */
  is_standby: boolean;
  standby_hint?: string | null;
}

/** 渠道目录（实时拉取结果，不缓存） */
export interface ChannelCatalog {
  platform_id: number;
  platform_name: string;
  is_baseline: boolean;
  channels: ChannelCatalogItem[];
  pulled_at: string;
}

const PlatformAPI = {
  listPlatform(query: PlatformPageQuery) {
    return request<ApiResponse<PageResult<PlatformTable>>>({
      url: `${API_PATH}/list`,
      method: "get",
      params: query,
    });
  },

  /** 获取全部平台（下拉用，不含分页） */
  platformOptions() {
    return request<ApiResponse<PlatformTable[]>>({
      url: `${API_PATH}/optionselect`,
      method: "get",
    });
  },

  detailPlatform(id: number) {
    return request<ApiResponse<PlatformTable>>({
      url: `${API_PATH}/detail/${id}`,
      method: "get",
    });
  },

  createPlatform(body: PlatformForm) {
    return request<ApiResponse<PlatformTable>>({
      url: `${API_PATH}/create`,
      method: "post",
      data: body,
    });
  },

  updatePlatform(id: number, body: PlatformForm) {
    return request<ApiResponse<PlatformTable>>({
      url: `${API_PATH}/update/${id}`,
      method: "put",
      data: body,
    });
  },

  deletePlatform(body: number[]) {
    return request<ApiResponse>({
      url: `${API_PATH}/delete`,
      method: "delete",
      data: body,
    });
  },

  /** 测试连接（用弹窗所填凭证，不依赖已保存平台） */
  testConnection(body: TestConnectionPayload) {
    return request<ApiResponse<TestConnectionResult>>({
      url: `${API_PATH}/test-connection`,
      method: "post",
      data: body,
    });
  },

  /** 测试已保存平台的连接（凭证回退库内，无需重填） */
  testSavedConnection(id: number, body?: TestConnectionPayload) {
    return request<ApiResponse<TestConnectionResult>>({
      url: `${API_PATH}/${id}/test-connection`,
      method: "post",
      data: body ?? {},
    });
  },

  /** 实时拉取平台渠道目录（不持久化；失败返回明确错误，不展示过期缓存） */
  getPlatformChannels(id: number) {
    return request<ApiResponse<ChannelCatalog>>({
      url: `${API_PATH}/${id}/channels`,
      method: "get",
    });
  },
};

export default PlatformAPI;
