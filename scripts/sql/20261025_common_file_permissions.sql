-- 公共文件上传/下载权限增量补丁（MySQL 8+）
-- 背景：module_common:file:upload / download 由头像、物品图片、凭证等共享接口使用，
--       但旧种子菜单没有对应按钮，普通角色无法在后台勾选，导致 403。
-- 用法：在目标库执行一次；脚本幂等，可重复执行。
-- 说明：按钮只能挂在 type=2 的菜单下。优先「物品管理」，否则回退到「用户管理」
--       （头像上传同样依赖公共文件接口）。

SET @parent_id := (
    SELECT id FROM sys_menu
    WHERE title = '物品管理' AND type = 2 AND is_deleted = 0
    ORDER BY id LIMIT 1
);
SET @parent_id := COALESCE(@parent_id, (
    SELECT id FROM sys_menu
    WHERE title = '用户管理' AND type = 2 AND is_deleted = 0
    ORDER BY id LIMIT 1
));

INSERT INTO sys_menu
    (name, type, `order`, permission, icon, route_name, route_path, component_path, redirect,
     hidden, keep_alive, always_show, title, params, affix, link, is_iframe, is_hide_tab,
     active_path, show_badge, show_text_badge, scope, status, description, parent_id, uuid,
     is_deleted, created_time, updated_time, deleted_time)
SELECT '文件上传', 3, 99, 'module_common:file:upload', NULL, NULL, NULL, NULL, NULL,
       0, 1, 0, '文件上传', NULL, 0, NULL, 0, 0, NULL, 0, NULL, 'web', 0,
       '公共文件上传权限：头像 / 物品图片 / 凭证等共享上传接口', @parent_id, UUID(),
       0, NOW(), NOW(), NULL
WHERE @parent_id IS NOT NULL
  AND NOT EXISTS (
      SELECT 1 FROM sys_menu
      WHERE permission = 'module_common:file:upload' AND is_deleted = 0
  );

INSERT INTO sys_menu
    (name, type, `order`, permission, icon, route_name, route_path, component_path, redirect,
     hidden, keep_alive, always_show, title, params, affix, link, is_iframe, is_hide_tab,
     active_path, show_badge, show_text_badge, scope, status, description, parent_id, uuid,
     is_deleted, created_time, updated_time, deleted_time)
SELECT '文件下载', 3, 99, 'module_common:file:download', NULL, NULL, NULL, NULL, NULL,
       0, 1, 0, '文件下载', NULL, 0, NULL, 0, 0, NULL, 0, NULL, 'web', 0,
       '公共文件下载权限：物品图片 / 凭证 / 导出附件等共享下载接口', @parent_id, UUID(),
       0, NOW(), NOW(), NULL
WHERE @parent_id IS NOT NULL
  AND NOT EXISTS (
      SELECT 1 FROM sys_menu
      WHERE permission = 'module_common:file:download' AND is_deleted = 0
  );

-- 添加后，在「角色管理 → 分配权限」勾选这两个按钮即可；超级管理员默认拥有全部权限。
