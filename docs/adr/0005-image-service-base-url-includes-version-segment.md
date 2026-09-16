# 绘图服务的 base_url 填完整前缀，与「平台」的 base_url 语义刻意不同

Status: accepted

仓库里 `平台.base_url` 的语义是**填到根、不带版本段**：`channel_eval/run/engine.py` 调用时自己拼 `{base_url}/v1/chat/completions`。而火山方舟文档给出的 Base URL 是 `https://ark.cn-beijing.volces.com/api/plan/v3`——**自带版本段**，且文档明确警告 `/plan` 不可与其他 API 接口混用。

**决定：绘图服务的 `base_url` 填完整 API 前缀（含 `/v1` 或 `/api/plan/v3`），代码只追加 `/images/generations`。** 管理员照厂商控制台文档复制粘贴即可。系统内两个 base_url 字段语义不同，因此绘图服务的表单必须写清占位提示，避免照着评估平台那边的习惯填。

理由：让管理员从火山的 Base URL 里手动砍掉 `/api/plan/v3` 是一道无谓的出错关口，而"后端检测到版本段就不重复追加"这类容错在 URL 形态千奇百怪时迟早猜错（apiworld 是 `/v1`、火山是 `/api/plan/v3`、将来还可能是别的）。语义一致但要求管理员记住一条仓库内部约定，不如语义直白。

**后果**：同一份后台里两个 base_url 字段的填写规则不同，这是刻意的、有待记的不一致。

**Considered Options**

- 沿用「平台」的语义（填到根），由接入方式决定补 `/api/plan/v3` 还是 `/v1`：管理员必须记住一条不在界面上的隐含约定，且接入方式与 URL 前缀被强行耦合——放弃。
- 后端容错：检测到已含版本段就不重复追加：本质是猜，猜错的表现是 404，排查成本高于收益——放弃。
- 统一改掉 `平台.base_url` 的语义使其一致：会牵动已上线的渠道评估模块与其历史配置——放弃。