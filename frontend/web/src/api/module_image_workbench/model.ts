import { request } from "@utils";

const API_PATH = "/image_workbench/models";

/**
 * 绘图模型的能力声明（每个模型逐一声明，不做代码内置匹配）。
 *
 * 它是**数据**：模型支持哪些出图参数、取值范围、默认值、何时可用、有哪些出图方式，
 * 全部落在这份清单里，前后端都读它。结构定义见后端
 * `app/modules/image_workbench/config/capability.py`（design D1/D2/D3），两处必须同步。
 *
 * 三条容易写歪的约定：
 * 1. **参数键就是上游字段路径，点号表示嵌套**：`optimize_prompt_options.mode` →
 *    `{"optimize_prompt_options": {"mode": ...}}`，不设「本地名 → 上游名」的映射表。
 * 2. **参数的发与不发只由清单决定，不由接入方式决定**：接入方式只决定参考图的上行形态。
 * 3. **出图方式是派生结果，不是独立字段**：请求里没有 `mode`，由 `modes[].trigger`
 *    从参数值派生（见 `views/image_workbench/job/capability.ts` 的求值器）。
 */

/** 面向用户的显示名（中英各一份，按当前语言取用） */
export interface CapabilityLabel {
  zh: string;
  en: string;
}

/** 参数的取值形态：既是前端渲染指令，也是校验依据（与后端 control 同源） */
export type ParamControl = "select" | "multiselect" | "switch" | "number" | "text";

/** 取值来源：上游文档 / 未实测的推断 / 已实测。未实测的在界面上必须可辨认 */
export type ParamSource = "documented" | "inferred" | "measured";

/** 本次请求的事实——**封闭**集合，只有张数与透明通道两项（design D2） */
export type RequestFact = "reference_count" | "reference_has_alpha";

/** 条件依赖里的单个条件：左值二选一（另一参数 / 请求事实），算子四选一 */
export interface CapabilityWhenClause {
  /** 左值：另一个参数的键（与 request 二选一） */
  param?: string;
  /** 左值：本次请求的事实（与 param 二选一） */
  request?: RequestFact;
  eq?: unknown;
  ne?: unknown;
  /** 属于其中之一（值为数组） */
  in?: unknown[];
  /** 不属于其中任何一个（值为数组） */
  not_in?: unknown[];
}

/** 条件依赖：一个**合取式**（`all` 是唯一的组合方式，不引入 or / not） */
export interface CapabilityWhen {
  all: CapabilityWhenClause[];
}

/** 参考图能力 */
export interface CapabilityReference {
  supported: boolean;
  max_images: number;
}

/** 出图方式的触发条件：某参数取某值即进入该方式 */
export interface CapabilityModeTrigger {
  param: string;
  eq: unknown;
}

/**
 * 靠坐标表达位置的出图方式，在提示词里的**占位形态**与**坐标标记写法**。
 *
 * `placeholder` 含一个 `{n}`（选区序号）、`template` 含 `{x1}{y1}{x2}{y2}`（四个坐标），
 * `range` 是坐标空间大小（合法坐标 `0 .. range-1`）。三种写法都由声明给出，前端只按它
 * 插入占位符、导出坐标，**不硬编码任何一句**——服务端渲染时读的是同一份声明。
 */
export interface CapabilityModeMarker {
  placeholder: string;
  template: string;
  range: number;
}

/**
 * 一种出图方式。
 *
 * `trigger` 为 null/缺省 = **固有方式**（不由参数触发）：交互编辑就是这样，上游没有对应
 * 参数，它靠提示词里的坐标标记表达。固有方式**必须**声明 `marker`——占位形态与标记写法
 * 是它与上游的全部约定；触发式的方式（图层拆分）与坐标无关，不需要也不禁止声明。
 */
export interface CapabilityMode {
  key: string;
  label: CapabilityLabel;
  trigger?: CapabilityModeTrigger | null;
  marker?: CapabilityModeMarker | null;
}

/** 该出图方式是否是靠坐标表达位置的那一种（判据是「有没有标记语法」而不是键名） */
export function isCoordinateMode(mode: CapabilityMode): boolean {
  return !!mode.marker;
}

/**
 * 该模型声明了交互编辑时返回它的标记语法，否则返回 null。
 *
 * 工作台的画布入口、坐标区间与占位符插入三处共用它——判据与后端 `coordinate_mode` 一致。
 */
export function findCoordinateMarker(
  capability?: { modes?: CapabilityMode[] } | null
): (CapabilityModeMarker & { modeKey: string }) | null {
  const mode = (capability?.modes ?? []).find((item) => item.marker);
  if (!mode?.marker) return null;
  return { ...mode.marker, modeKey: mode.key };
}

/** 某参数在某出图方式下对取值域与默认值的覆盖（缺省项沿用条目自身） */
export interface CapabilityParamOverride {
  allowed?: unknown[] | null;
  default?: unknown;
}

/** 出图参数条目——参数的全部知识集中在这一处 */
export interface CapabilityParam {
  /** 参数键（同时是上游请求体的字段路径，点号表示嵌套） */
  key: string;
  label: CapabilityLabel;
  control: ParamControl;
  /** 取值域（select / multiselect 必填） */
  allowed?: unknown[] | null;
  /** select 时是否额外允许填自定义「宽x高」 */
  allow_custom?: boolean;
  default: unknown;
  source: ParamSource;
  when?: CapabilityWhen | null;
  overrides?: Record<string, CapabilityParamOverride>;
}

/** 能力声明：参数条目的顺序即前端呈现顺序；出图方式的顺序即优先级 */
export interface ModelCapability {
  reference: CapabilityReference;
  modes: CapabilityMode[];
  params: CapabilityParam[];
}

/** 新建模型时填入的能力声明默认值（与后端 schema 默认值一致：缺省不支持参考图） */
export function createDefaultCapability(): ModelCapability {
  return {
    reference: { supported: false, max_images: 0 },
    modes: [],
    params: [],
  };
}

/** 新建参数条目时的默认形态（control 为 select，取值域与默认值待填） */
export function createDefaultParam(): CapabilityParam {
  return {
    key: "",
    label: { zh: "", en: "" },
    control: "select",
    allowed: [],
    allow_custom: false,
    default: "",
    source: "documented",
    when: null,
    overrides: {},
  };
}

/**
 * 新建出图方式时的默认形态（默认不由参数触发，即固有方式）。
 *
 * 带上默认标记语法：固有方式在后端**必须**声明 marker，不给默认值的话，管理员新加一条方式
 * 就会被后端以「缺标记语法」拒掉，而他并没有做错什么。
 */
export function createDefaultMode(): CapabilityMode {
  return {
    key: "",
    label: { zh: "", en: "" },
    trigger: null,
    marker: createDefaultMarker(),
  };
}

/** 新建标记语法时的默认形态（与预置的交互编辑一致：归一化到 1000×1000） */
export function createDefaultMarker(): CapabilityModeMarker {
  return {
    placeholder: "区域{n}",
    template: "<bbox>{x1} {y1} {x2} {y2}</bbox>",
    range: 1000,
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
