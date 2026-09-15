# 用 new-api「指定渠道」实现渠道路由定向

Status: accepted

渠道评估要求"选中的「渠道 × 模型」组合每个都测到、每个恰好 N 次"（先选渠道、再选该渠道下的模型；选中渠道不代表测其全部模型）。但普通客户端请求无法指定上游渠道：new-api 按「分组 → 优先级 → 权重 + 渠道亲和」自动路由；同优先级时落点随机且带粘性，优先级分层时备用渠道永远接不到流量。而 apiworld 承载线上真实流量，禁止在测试期间调整优先级或启停渠道。

**决定：指定渠道（pin）。** new-api v1.0.0-rc.21+ 原生支持管理员令牌的「指定渠道」：请求头 `Authorization: Bearer <key>-<渠道ID>` 即把请求 pin 到该渠道（源码口径，见下）。评估系统在**每次运行创建时经管理接口创建 1 个专用管理员令牌**（命名约定 `eval-run{运行ID}`，group=default，属平台管理用户，与日常测试令牌隔离），本运行全部「渠道 × 模型」组合复用它并 pin 到各自渠道；日志按该令牌名过滤 + request_id 1:1 配对，归因 100% 准确。对 apiworld 的写操作仅此一项，永不触碰优先级、权重、启停、模型映射。

**机制证据（new-api v1.0.0-rc.21 源码）**：`middleware/auth.go` 先剥离 `sk-` 前缀再按 `-` 切分，`parts[0]` 为真 key、`parts[1]` 为渠道ID；令牌属主非管理员时 403「普通用户不支持指定渠道」；`middleware/distributor.go` pin 后直取渠道（`strconv.Atoi(parts[1])` → `GetChannelById`，仅校验存在+启用，不走分组路由、不做模型列表校验）；`controller/relay.go` pin 时禁止重试 → 归因无漂移。线上实测（apiworld）：`Bearer <管理员令牌>-999999` → 400「无效的渠道 Id」、`Bearer <管理员令牌>-71` → 请求成功且日志 `token_name=<令牌名>, channel_id=71`。

**Considered Options**

- 分组定向（每渠道一个 `eval-ch{ID}` 分组标签 + 绑定令牌）：new-api 转发前还要求令牌分组同时在 `UserUsableGroups` 与 `GroupRatio` 中，每新增待测渠道都需平台侧加两条设置（apiworld 线上实测 403「无权访问 eval-ch71 分组」）；且每渠道都要建令牌，写操作随渠道数线性增长——放弃。
- 日志归因（发普通请求，事后按日志中的 channel_name 归到各渠道）：覆盖无保证，备用渠道结构性缺席——放弃。
- new-api 自带测渠道接口 `/api/channel/test/{id}`：计时发生在服务端内部，丢失用户→平台网络段的客户端视角——放弃。
- 测试期间程序自动调优先级/启停：对线上流量有直接影响——放弃。
