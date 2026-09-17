import { request } from "@utils";

const API_PATH = "/image_workbench/models";

/**
 * 绘图模型的能力声明（每个模型逐一声明，不做代码内置匹配）。
 *
 * 尺寸取值原样提交给上游、不做归一化翻译：上游有的收档位字符串（1K/2K），
 * 有的收固定像素枚举，硬套一层映射会在像素与宽高比约束上碎掉。
 */
export interface ModelCapability {
  /** 可选尺寸取值（档位字符串或 "宽x高"），空数组表示该模型无尺寸概念 */
  sizes: string[];
  /** 是否额外允许填自定义 "宽x高" */
  allow_custom_size: boolean;
  /** 是否支持参考图 */
  support_reference: boolean;
  /** 参考图张数上限 */
  max_reference_images: number;
  /** 可选输出格式 */
  output_formats: string[];
  default_output_format: string;
  /** 是否加水印的默认值（上游默认加水印，故此处默认 false） */
  watermark_default: boolean;
  /** 原样透传给上游的默认参数 */
  extra_params: Record<string, unknown>;
}

/** 新建模型时填入的能力声明默认值（与后端 schema 默认值一致） */
export function createDefaultCapability(): ModelCapability {
  return {
    sizes: [],
    allow_custom_size: false,
    support_reference: true,
    max_reference_images: 10,
    output_formats: ["png", "jpeg"],
    default_output_format: "png",
    watermark_default: false,
    extra_params: {},
  };
}

/** 绘图模型分页查询参数 */
export interface ModelPageQuery extends PageQuery {
  service_id?: number;
  model_id?: string;
  display_name?: string;
  status?: number;
  /** 排序字段 JSON 字符串，如 [{"id":"desc"}] */
  order_by?: string;
}

/** 绘图模型列表项 */
export interface ModelTable extends BaseType {
  service_id: number;
  service_name?: string | null;
  /** 上游真实模型标识，原样存取不做规范化 */
  model_id: string;
  display_name: string;
  capability: ModelCapability;
  status: number;
  remark?: string | null;
}

/** 绘图模型创建/编辑表单（能力声明整体覆盖，不做合并） */
export interface ModelForm extends BaseFormType {
  service_id?: number;
  model_id?: string;
  display_name?: string;
  capability?: ModelCapability;
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
