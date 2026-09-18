/**
 * 绘图工作台 - 能力声明的**求值**（后端 `config/capability.py` 的同一套规则，此处镜像一份）。
 *
 * 前端必须自己算出「本次请求条件下每个参数是否可用、生效的取值域与默认值是什么」：
 * 规格要求参数的可用性随本次请求的输入**当场可见**（上传参考图后「图片透明通道」立刻
 * 变为可用），而不是等提交后被后端拒绝。**这里的规则与后端逐条对应，改一处必须同步另一处。**
 *
 * 三条与后端一致的约定：
 * 1. 出图方式是**派生结果**（design D3）：先用参数值定出命中的方式，再按该方式应用覆盖；
 * 2. `when` 的求值用「提交值，未提交则生效默认值」代入；
 * 3. 不认识的左值（含缺失的请求事实）一律按**不满足**处理（保守置灰，不猜）。
 */
import type {
  CapabilityMode,
  CapabilityParam,
  CapabilityWhenClause,
  ModelCapability,
  ParamControl,
} from "@/api/module_image_workbench/model";

/** 本次请求的事实——集合封闭，只有这两项（design D2） */
export interface RequestFacts {
  reference_count: number;
  reference_has_alpha: boolean;
}

/** 某参数在本次请求条件下的解析结果 */
export interface ResolvedParam {
  key: string;
  control: ParamControl;
  allowed: unknown[];
  allowCustom: boolean;
  /** 本次条件下生效的默认值（含按出图方式的覆盖） */
  defaultValue: unknown;
  available: boolean;
  /** 当前**不满足**的条件；由调用方按语言渲染成「为何不可用」 */
  unsatisfied: WhenClauseView[];
  param: CapabilityParam;
}

/** 条件依赖里某个条件在界面上的样子 */
export interface WhenClauseView {
  kind: "param" | "request";
  /** 参数的键，或请求事实名 */
  key: string;
  operator: "eq" | "ne" | "in" | "not_in";
  right: unknown;
}

export interface CapabilityEvaluation {
  params: ResolvedParam[];
  modes: CapabilityMode[];
  /** 本次条件下命中的出图方式标识；未命中为 null */
  activeMode: string | null;
}

/** 自定义尺寸的形态：「宽x高」的像素值（与后端 `_CUSTOM_VALUE_PATTERN` 一致） */
const CUSTOM_VALUE_PATTERN = /^\d{2,5}[xX*]\d{2,5}$/;

const MISSING = Symbol("missing");

/** 取值相等：按 JSON 形态比较，布尔与数字不互认（1 与 true 是两回事） */
function sameValue(left: unknown, right: unknown): boolean {
  return JSON.stringify(left ?? null) === JSON.stringify(right ?? null);
}

export function emptyFacts(): RequestFacts {
  return { reference_count: 0, reference_has_alpha: false };
}

/**
 * 参考图 → 本次请求的事实。
 *
 * `reference_has_alpha` 与后端一致：**全部**参考图都带透明通道才为真，没有参考图时为假。
 */
export function buildFacts(references: { hasAlpha: boolean }[]): RequestFacts {
  return {
    reference_count: references.length,
    reference_has_alpha: references.length > 0 && references.every((item) => item.hasAlpha),
  };
}

/** 取值是否落在**本次条件下生效的**取值域内（镜像后端的 `_value_matches`） */
export function acceptsValue(
  control: ParamControl,
  allowed: unknown[],
  allowCustom: boolean,
  value: unknown
): boolean {
  if (control === "switch") return typeof value === "boolean";
  if (control === "number") {
    if (typeof value !== "number" || Number.isNaN(value)) return false;
  } else if (control === "text") {
    if (typeof value !== "string") return false;
  } else if (allowCustom && typeof value === "string" && CUSTOM_VALUE_PATTERN.test(value.trim())) {
    return true;
  }
  if (allowed.length > 0) return allowed.some((item) => sameValue(item, value));
  // number / text 未声明取值域即无界；select 声明空取值域不接受任何值
  return control === "number" || control === "text";
}

function clauseOf(clause: CapabilityWhenClause): WhenClauseView {
  const operator: WhenClauseView["operator"] =
    clause.eq !== undefined
      ? "eq"
      : clause.ne !== undefined
        ? "ne"
        : clause.in !== undefined
          ? "in"
          : "not_in";
  const right = clause.eq ?? clause.ne ?? clause.in ?? clause.not_in;
  return clause.param !== undefined
    ? { kind: "param", key: clause.param, operator, right }
    : { kind: "request", key: clause.request ?? "", operator, right };
}

/** 单个条件的求值：左值不认识或事实缺失即不满足（保守置灰） */
function evaluateClause(
  clause: CapabilityWhenClause,
  values: Map<string, unknown>,
  facts: RequestFacts
): boolean {
  let left: unknown = MISSING;
  if (clause.param !== undefined) {
    left = values.has(clause.param) ? values.get(clause.param) : MISSING;
  } else if (clause.request !== undefined && clause.request in facts) {
    left = facts[clause.request];
  }
  if (left === MISSING) return false;

  const { operator, right } = clauseOf(clause);
  if (operator === "eq") return sameValue(left, right);
  if (operator === "ne") return !sameValue(left, right);
  const list = Array.isArray(right) ? right : [];
  if (operator === "in") return list.some((item) => sameValue(item, left));
  return !list.some((item) => sameValue(item, left));
}

/**
 * 求值：算出本次请求条件下每个参数的可用性与生效的取值域 / 默认值。
 *
 * `values` 传「用户改动过的参数」（与后端提交的 `params` 同一份口径），未提交的以
 * 条目默认值代入——这是 design D5 明写的用法：未提交的参数以默认值参与求值，不等于
 * 要把默认值写进最后的结果。
 */
export function evaluateCapability(
  capability: ModelCapability,
  options: { values?: Record<string, unknown>; facts?: RequestFacts } = {}
): CapabilityEvaluation {
  const values = options.values ?? {};
  const facts = options.facts ?? emptyFacts();
  const params = capability.params ?? [];
  const modes = capability.modes ?? [];

  // 第一步：用「提交值，未提交则条目默认值」定出命中的出图方式
  const baseValues = new Map<string, unknown>();
  for (const param of params) {
    baseValues.set(param.key, values[param.key] !== undefined ? values[param.key] : param.default);
  }
  let activeMode: string | null = null;
  for (const mode of modes) {
    if (!mode.trigger) continue;
    if (sameValue(baseValues.get(mode.trigger.param), mode.trigger.eq)) {
      activeMode = mode.key;
      break;
    }
  }

  // 第二步：应用该出图方式对本条目取值域与默认值的覆盖
  const resolvedValues = new Map<string, unknown>();
  const effective = params.map((param) => {
    const override = activeMode ? param.overrides?.[activeMode] : undefined;
    const allowed = override?.allowed != null ? [...override.allowed] : [...(param.allowed ?? [])];
    const defaultValue =
      override?.default !== undefined && override.default !== null
        ? override.default
        : param.default;
    resolvedValues.set(
      param.key,
      values[param.key] !== undefined ? values[param.key] : defaultValue
    );
    return { param, allowed, defaultValue, allowCustom: param.allow_custom ?? false };
  });

  // 第三步：逐条求 when
  const result: ResolvedParam[] = effective.map((item) => {
    const clauses = item.param.when?.all ?? [];
    const unsatisfied = clauses
      .filter((clause) => !evaluateClause(clause, resolvedValues, facts))
      .map((clause) => clauseOf(clause));
    return {
      key: item.param.key,
      control: item.param.control,
      allowed: item.allowed,
      allowCustom: item.allowCustom,
      defaultValue: item.defaultValue,
      available: unsatisfied.length === 0,
      unsatisfied,
      param: item.param,
    };
  });

  return { params: result, modes, activeMode };
}
