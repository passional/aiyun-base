import type { ColumnOption } from "@/types/component";

/** 常见表头中文 → i18n key；未命中时保留原文 */
const LABEL_KEYS: Record<string, string> = {
  序号: "common.table.index",
  操作: "common.table.operation",
  创建时间: "common.table.createdAt",
  更新时间: "common.table.updatedAt",
  状态: "common.table.status",
  描述: "common.table.description",
  排序: "common.table.sort",
  名称: "common.table.name",
  编码: "common.table.code",
  备注: "common.table.remark",
  账号: "common.account",
  用户名: "common.username",
  手机号: "common.phone",
  邮箱: "common.email",
  部门: "common.dept",
  角色: "common.role",
  岗位: "common.position",
  密码: "common.password",
  是否超管: "common.superuser",
  性别: "common.gender",
};

export function translateColumnLabels<T>(
  columns: ColumnOption<T>[],
  t: (key: string) => string
): ColumnOption<T>[] {
  return columns.map((col) => {
    const key = typeof col.label === "string" ? LABEL_KEYS[col.label] : undefined;
    return key ? { ...col, label: t(key) } : col;
  });
}

const STATUS_KEYS: Record<string, string> = {
  启用: "common.enabled",
  停用: "common.disabled",
  成功: "common.success",
  失败: "common.failed",
  是: "common.yes",
  否: "common.no",
  男: "common.male",
  女: "common.female",
  草稿: "common.draft",
  已发布: "common.published",
  已回滚: "common.rolledBack",
  未知: "common.unknown",
  全部数据权限: "common.dataScopeAll",
  仅本人数据权限: "common.dataScopeSelf",
  本部门及以下数据权限: "common.dataScopeDept",
  目录: "common.catalog",
  菜单: "common.menu",
  按钮: "common.button",
  外链: "common.extlink",
  通知: "common.notification",
  公告: "common.announcement",
  本地: "common.local",
  链式: "common.chain",
  多目标: "common.multiTarget",
};

export function translateStatusText(text: string, t: (key: string) => string): string {
  const key = STATUS_KEYS[text];
  return key ? t(key) : text;
}

const PLACEHOLDER_KEYS: Record<string, string> = {
  请输入账号: "common.placeholder.account",
  请输入用户名: "common.placeholder.username",
  请输入手机号码: "common.placeholder.phone",
  请输入邮箱: "common.placeholder.email",
  请输入密码: "common.placeholder.password",
  请输入描述: "common.placeholder.description",
  请选择性别: "common.placeholder.gender",
  请选择上级部门: "common.placeholder.dept",
  请选择角色: "common.placeholder.role",
  请选择岗位: "common.placeholder.position",
  请输入角色名称: "common.placeholder.roleName",
  请输入岗位名称: "common.placeholder.positionName",
  请输入菜单名称: "common.placeholder.menuName",
  请输入部门名称: "common.placeholder.deptName",
  请输入通知标题: "common.placeholder.noticeTitle",
  请输入请求路径: "common.placeholder.requestPath",
};

export function translateFormLabels<T extends object>(items: T[], t: (key: string) => string): T[] {
  return items.map((item) => {
    const record = item as { label?: unknown; placeholder?: unknown };
    const labelKey = typeof record.label === "string" ? LABEL_KEYS[record.label] : undefined;
    const placeholderKey =
      typeof record.placeholder === "string" ? PLACEHOLDER_KEYS[record.placeholder] : undefined;
    const props = (record as { props?: { options?: Array<{ label?: unknown }> } }).props;
    const options = Array.isArray(props?.options)
      ? props.options.map((opt) =>
          typeof opt.label === "string" ? { ...opt, label: translateStatusText(opt.label, t) } : opt
        )
      : undefined;

    if (!labelKey && !placeholderKey && !options?.some((o, i) => o !== props?.options?.[i])) {
      return item;
    }
    return {
      ...item,
      ...(labelKey ? { label: t(labelKey) } : {}),
      ...(placeholderKey ? { placeholder: t(placeholderKey) } : {}),
      ...(options ? { props: { ...props, options } } : {}),
    } as T;
  });
}
