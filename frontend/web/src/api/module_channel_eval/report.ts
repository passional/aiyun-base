import { request } from "@utils";

const API_PATH = "/channel_eval/reports";

/** 报告分页查询参数 */
export interface ReportPageQuery extends PageQuery {
  name?: string;
  /** 排序字段 JSON 字符串，如 [{"id":"desc"}] */
  order_by?: string;
}

/** 报告列表项（报告=选择+备注，不复制测量数据） */
export interface ReportTable extends BaseType {
  name: string;
  /** 所选测试运行ID列表（一至多次，可跨平台） */
  run_ids: number[];
  /** 基准运行ID（须为基准渠道的成功运行） */
  baseline_run_id?: number | null;
  note?: string | null;
  status: number;
}

/** 报告创建/编辑表单 */
export interface ReportForm extends BaseFormType {
  name?: string;
  run_ids?: number[];
  baseline_run_id?: number | null;
  note?: string | null;
}

/** 报告中运行的标识信息 */
export interface ReportRunInfo {
  id: number;
  platform_id: number;
  platform_name?: string | null;
  status: string;
  is_baseline: boolean;
  created_time?: string | null;
  combo_count: number;
}

/** 对比视图中的单组合聚合（客户端实测 + 后台日志 + 交叉对比 + 基准差值） */
export interface ReportComboView {
  run_id: number;
  platform_id: number;
  platform_name?: string | null;
  channel_id: number;
  channel_name: string;
  model: string;
  is_baseline: boolean;
  planned: number;
  success: number;
  failed: number;
  anomaly: number;
  executed: number;
  success_rate?: number | null;
  ttft_avg_ms?: number | null;
  total_avg_ms?: number | null;
  tps_avg?: number | null;
  ttft_with_anomaly_avg_ms?: number | null;
  total_with_anomaly_avg_ms?: number | null;
  be_matched_count: number;
  be_frt_avg_ms?: number | null;
  be_use_time_avg_ms?: number | null;
  be_tokens_out_avg?: number | null;
  cross_ttft_diff_ms?: number | null;
  cross_total_diff_ms?: number | null;
  ttft_vs_baseline_ms?: number | null;
  total_vs_baseline_ms?: number | null;
}

/** 按模型分组的对比视图 */
export interface ReportModelGroup {
  model: string;
  baseline?: ReportComboView | null;
  has_baseline: boolean;
  combos: ReportComboView[];
}

/** 异常清单条目（超阈值样本与失败样本） */
export interface ReportAnomalyItem {
  kind: string;
  run_id: number;
  platform_name?: string | null;
  channel_id: number;
  channel_name: string;
  model: string;
  seq: number;
  is_warmup: boolean;
  status: string;
  ttft_ms?: number | null;
  total_ms?: number | null;
  error?: string | null;
  be_matched: boolean;
}

/** 自动结论（仅阈值事实，无评价性措辞） */
export interface ReportConclusion {
  content: string;
}

/** 报告视图聚合（Web 视图与 HTML 导出共用） */
export interface ReportView {
  report: ReportTable;
  runs: ReportRunInfo[];
  baseline_run?: ReportRunInfo | null;
  models: ReportModelGroup[];
  anomalies: ReportAnomalyItem[];
  conclusions: ReportConclusion[];
  generated_at: string;
}

const ReportAPI = {
  listReport(query: ReportPageQuery) {
    return request<ApiResponse<PageResult<ReportTable>>>({
      url: `${API_PATH}/list`,
      method: "get",
      params: query,
    });
  },

  detailReport(id: number) {
    return request<ApiResponse<ReportTable>>({
      url: `${API_PATH}/detail/${id}`,
      method: "get",
    });
  },

  createReport(body: ReportForm) {
    return request<ApiResponse<ReportTable>>({
      url: `${API_PATH}/create`,
      method: "post",
      data: body,
    });
  },

  updateReport(id: number, body: ReportForm) {
    return request<ApiResponse<ReportTable>>({
      url: `${API_PATH}/update/${id}`,
      method: "put",
      data: body,
    });
  },

  deleteReport(body: number[]) {
    return request<ApiResponse>({
      url: `${API_PATH}/delete`,
      method: "delete",
      data: body,
    });
  },

  /** 报告视图聚合（按模型分组并列各运行与基准 + 交叉对比 + 异常清单 + 自动结论） */
  getReportView(id: number) {
    return request<ApiResponse<ReportView>>({
      url: `${API_PATH}/${id}/view`,
      method: "get",
    });
  },

  /** 导出自包含 HTML（单文件、零外部依赖；导出即冻结当时引用数据） */
  exportReport(id: number) {
    return request<Blob>({
      url: `${API_PATH}/${id}/export`,
      method: "get",
      responseType: "blob",
    });
  },
};

export default ReportAPI;
