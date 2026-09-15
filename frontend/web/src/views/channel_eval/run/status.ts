import { $t } from "@/locales";

export type RunStatusTagType = "primary" | "success" | "warning" | "danger" | "info";

/** 运行状态文案（状态机：pending/running/cancelling/success/failed/cancelled） */
export function runStatusText(status?: string): string {
  switch (status) {
    case "pending":
      return $t("channel_eval.run.statusPending");
    case "running":
      return $t("channel_eval.run.statusRunning");
    case "cancelling":
      return $t("channel_eval.run.statusCancelling");
    case "success":
      return $t("channel_eval.run.statusSuccess");
    case "failed":
      return $t("channel_eval.run.statusFailed");
    case "cancelled":
      return $t("channel_eval.run.statusCancelled");
    default:
      return status ?? "—";
  }
}

/** 运行状态标签色 */
export function runStatusTagType(status?: string): RunStatusTagType {
  switch (status) {
    case "success":
      return "success";
    case "failed":
      return "danger";
    case "cancelling":
      return "warning";
    case "running":
      return "primary";
    default:
      return "info";
  }
}

/** 表格列 status 快捷配置映射（配合 resolveStatusColumns 使用） */
export function runStatusMap(): Record<string, { type: RunStatusTagType; text: string }> {
  return {
    pending: { type: "info", text: runStatusText("pending") },
    running: { type: "primary", text: runStatusText("running") },
    cancelling: { type: "warning", text: runStatusText("cancelling") },
    success: { type: "success", text: runStatusText("success") },
    failed: { type: "danger", text: runStatusText("failed") },
    cancelled: { type: "info", text: runStatusText("cancelled") },
  };
}
