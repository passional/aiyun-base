import { request } from "@utils";

const API_PATH = "/channel_eval/model-maps";

/** 规范模型分页查询参数 */
export interface CanonicalModelPageQuery extends PageQuery {
  /** 显示名（模糊匹配） */
  display_name?: string;
  /** 系列（模糊匹配） */
  family?: string;
  /** 排序字段 JSON 字符串，如 [{"id":"desc"}] */
  order_by?: string;
}

/** 渠道模型串（别名）归属记录 */
export interface ModelAlias extends BaseType {
  /** 所属规范模型ID */
  canonical_id: number;
  /** 渠道侧的模型串 */
  alias: string;
  /** 来源：manual=人工录入 / auto=确认建议 */
  source: string;
}

/** 规范模型列表项 */
export interface CanonicalModelTable extends BaseType {
  /** 显示名（报告中呈现） */
  display_name: string;
  /** 系列（如 Claude/GPT） */
  family?: string | null;
  remark?: string | null;
  /** 状态(0:正常 1:停用) */
  status: number;
  /** 归属的渠道模型串 */
  aliases: ModelAlias[];
}

/** 规范模型创建/编辑表单（别名以整体提交，服务端做增删同步） */
export interface CanonicalModelForm extends BaseFormType {
  display_name?: string;
  family?: string | null;
  remark?: string | null;
  /** 渠道模型串列表（下拉可选可录入） */
  aliases?: string[];
}

/** 一个渠道模型串在哪儿出现过（供人工判断是否同族） */
export interface AliasObservation {
  alias: string;
  /** 出现过的平台名 */
  platforms: string[];
  /** 出现过的渠道名 */
  channels: string[];
  /** 是否落在某条自动建议里 */
  has_suggestion: boolean;
}

/** 一条「疑似同族」建议 */
export interface SuggestionGroup {
  /** 建议的规范模型显示名 */
  suggested_name: string;
  /** 归一化键 */
  normalized: string;
  /** 组内各串及其来源 */
  observations: AliasObservation[];
  /** 判断依据代码(prefix/separator/case/date) */
  reason_codes: string[];
  /** 判断依据中文标签 */
  reasons: string[];
}

/** 未确认归属的渠道模型串清单 */
export interface UnmappedModels {
  observations: AliasObservation[];
  /** 其中落在建议里的条数 */
  suggestion_count: number;
}

/** 确认一条自动建议：一组串落成一个规范模型 */
export interface ConfirmSuggestionForm {
  display_name: string;
  family?: string | null;
  /** 该组包含的渠道模型串 */
  aliases: string[];
}

const ModelMapAPI = {
  listCanonicalModel(query: CanonicalModelPageQuery) {
    return request<ApiResponse<PageResult<CanonicalModelTable>>>({
      url: `${API_PATH}/list`,
      method: "get",
      params: query,
    });
  },

  detailCanonicalModel(id: number) {
    return request<ApiResponse<CanonicalModelTable>>({
      url: `${API_PATH}/detail/${id}`,
      method: "get",
    });
  },

  createCanonicalModel(body: CanonicalModelForm) {
    return request<ApiResponse<CanonicalModelTable>>({
      url: `${API_PATH}/create`,
      method: "post",
      data: body,
    });
  },

  updateCanonicalModel(id: number, body: CanonicalModelForm) {
    return request<ApiResponse<CanonicalModelTable>>({
      url: `${API_PATH}/update/${id}`,
      method: "put",
      data: body,
    });
  },

  deleteCanonicalModel(body: number[]) {
    return request<ApiResponse>({
      url: `${API_PATH}/delete`,
      method: "delete",
      data: body,
    });
  },

  /** 查询疑似同族的自动建议（不落库，确认后才生成规范模型） */
  getSuggestions() {
    return request<ApiResponse<SuggestionGroup[]>>({
      url: `${API_PATH}/suggestions`,
      method: "get",
    });
  },

  /** 查询尚未确认归属的渠道模型串 */
  getUnmapped() {
    return request<ApiResponse<UnmappedModels>>({
      url: `${API_PATH}/unmapped`,
      method: "get",
    });
  },

  /** 确认一条建议：整组渠道模型串归属到新的规范模型 */
  confirmSuggestion(body: ConfirmSuggestionForm) {
    return request<ApiResponse<CanonicalModelTable>>({
      url: `${API_PATH}/confirm`,
      method: "post",
      data: body,
    });
  },
};

export default ModelMapAPI;
