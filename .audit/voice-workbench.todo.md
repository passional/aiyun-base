# 语音工作台（ASR）执行清单

Playbook: Feature

## Playbook steps

- [x] 1. `how` over the affected subsystem.
- [x] 2. `architect` for parallel design exploration.
  - skip: 设计已由 `docs/voice-workbench-design.md` + ADR-0010/0011/0012 冻结，没有待探索的设计分叉。
- [x] 3. Write the throughput checkpoint as four todo items.
- [x] 4. Delegate code-writing to a subagent.
- [x] 5. Verify on the matching surface.
- [x] 6. Rebase into small, ordered commits.
- [x] 7. If the design is contested, `interrogate` before shipping.
  - skip: 设计已在文档中确认，无争议。
- [x] 8. Run **Opening a PR**.
  - skip: 用户要求实现，未要求开 PR；本地分支交付。

## Throughput checkpoint

- [x] Blocking first steps. 冻结数据形状、API 契约与上游二进制帧格式（主线程完成）。
- [x] Independent workstreams. 后端模块与前端模块文件集不相交，两个工作树并行。
- [x] Shared mutable state. 后端工作树独占 migration/seed/menu/router/startup 文件；前端独占 `api/module_voice_workbench` 与 `views/voice_workbench`、i18n。
- [x] Smallest safe decomposition. 两个写手即可：协议与安全逻辑统一在后端 owner 内完成；主线程负责集成、审查与真实验证。

## Task-specific

- [x] 后端：模块骨架、数据表、迁移、种子、菜单、路由、启动恢复
- [x] 后端：配置 CRUD（加密/掩码/删除保护）与测试
- [x] 后端：WAV 校验纯函数与测试
- [x] 后端：火山 WebSocket 客户端与协议测试
- [x] 后端：转写接口（校验、Redis 串行锁、任务记录）与 API 测试
- [x] 前端：三个 API 客户端
- [x] 前端：语音服务配置页
- [x] 前端：工作台页（转码、批量、结果、历史、导出）
- [x] 前端：中英文文案
- [x] 集成：合并两个工作树到 `feat/voice-workbench`
- [x] 验证：后端 `pytest tests/voice_workbench` + 迁移零差异（全套 603 passed）
- [x] 验证：前端 `ts:check` + `vitest` + `lint`（82 passed，build 成功）
- [x] 验证：真实应用冒烟（MySQL 临时库：种子、填 key、可选模型、失败落库、401、重启恢复）
- [x] 决策日志审计 + 独立模型复核（审查发现并修复重复 data 块绕过时长上限；605 backend / 82 frontend 全绿）