# 本地开发完整指南（新手向）

> 本文档帮你把 FastapiAdmin 全栈项目在**你自己电脑上**跑起来。
> 读完并照着做，你就能打开浏览器看到一个能登录、能点菜单的管理后台。
> 不用背，遇到"这个文件是干嘛的"回来查这张表就行。

---

## 0. 先说清楚：这个项目由几部分组成

整个项目 = **1 个后端 + 3 个前端** + **2 个数据库软件**，它们各是各的进程：

```
┌─────────────────────────────────────────────────────────────┐
│  你电脑（本机 localhost）                                     │
│                                                             │
│  ┌────────────┐  连接   ┌──────────────┐                    │
│  │  MySQL 数据库 │◄────────│   后端 FastAPI  │                │
│  │  (Docker容器)│         │  端口 8001     │                │
│  └────────────┘         └──────┬───────┘                    │
│  ┌────────────┐  连接           │ 提供 /api/v1 接口           │
│  │  Redis 缓存  │◄──────────────┤                            │
│  │  (Docker容器)│               │                            │
│  └────────────┘               ▼                            │
│                      ┌──────────────────┐                   │
│                      │ 管理后台前端 (Vue3) │  ← 你平时写页面在这   │
│                      │ 浏览器打开 5180 端口 │                   │
│                      └──────────────────┘                   │
│                      ┌──────────────────┐                   │
│                      │ 文档站 (VitePress) │  ← 可后开，非必需    │
│                      └──────────────────┘                   │
│                      ┌──────────────────┐                   │
│                      │ 小程序 (uni-app)   │  ← 可后开，非必需    │
│                      └──────────────────┘                   │
└─────────────────────────────────────────────────────────────┘
```

- **后端**：用 Python + `uv` 启动，负责所有业务逻辑，提供接口。
- **管理后台前端**：用 Node + `pnpm` 启动，是你打开浏览器看到的管理界面，**日常开发 90% 在这**。
- **MySQL / Redis**：数据库和缓存，本项目用 Docker 容器方式跑（这样你不用在自己电脑装 MySQL/Redis）。

> 术语提醒：这里的"本地开发"指的是**前端后端在你电脑上直接跑**（改代码即时生效），
> 而 MySQL/Redis 这种"基础设施"用 Docker 跑。这是本项目最推荐的方式，和 README 里说的一致。

---

## 1. 配置文件全景图（先看这张表，晕头转向就查它）

项目里有**三个文件夹**放配置文件，分别管三件不同的事。你只需要分清它们，就不会晕：

| 文件 | 管什么 | 你需要改吗 |
|------|--------|-----------|
| `docker/.env` | **创建** MySQL/Redis 容器时用的密码（相当于"给数据库设密码"） | ⚠️ 已经填好，看一眼即可 |
| `backend/env/.env.dev` | 后端**怎么连接**数据库/Redis（相当于"用密码登录"） | ✅ **必须填**，要和上面一致 |
| `frontend/web/.env` + `.env.development` | 前端访问后端的地址、端口等 | ✅ 项目已给好默认值，**通常不用动** |
| `frontend/app/.env.development` | 小程序/移动端连哪个后端 | 只在开发小程序时才需要动 |
| `frontend/docs/` | 文档站，无配置 | 不开发文档就不用管 |

**最容易搞混、也是新手最常踩的坑：**

```
docker/.env 里的密码  =  后端连数据库时输入的密码  （这俩必须一样！）
```

`docker/.env` 是你"给 MySQL 设的密码"，`backend/env/.env.dev` 是你"拿密码去登录"。
两个文件里的密码不一致 = 后端连不上数据库 = 启动报错。下面第 4 节会带你一步步填。

---

## 2. 零基础完整流程（照抄就行）

一共 6 步，按顺序做。做完第 5 步其实就已经能玩了，第 6 步是加分项。

### 第 1 步：确认工具都装好了

打开终端，逐个敲，确认都有输出（没有就先去装）：

```bash
uv --version      # 后端依赖管理工具（Python 项目的"npm"）
python3 --version # 需要 3.12+
node --version    # 需要 20+
pnpm --version    # 前端包管理器
docker --version  # 跑 MySQL/Redis 用
```

> 没有的怎么办：
> - `uv`：`curl -LsSf https://astral.sh/uv/install.sh | sh`（或去 GitHub 下载）
> - `pnpm`：`npm install -g pnpm`
> - `docker`：去 [docker.com](https://www.docker.com/) 装 Docker Desktop

### 第 2 步：启动 MySQL 和 Redis（基础设施）

进入 `docker/` 目录，用一条命令把数据库和缓存容器拉起来：

```bash
cd docker
docker compose --env-file .env up -d mysql redis
```

> 第一次会下载镜像，稍等。之后每次开电脑，如果容器没在跑，再执行一次这条命令即可。
>
> 验证是否成功（两条都要显示 healthy 或 Up）：
> ```bash
> docker compose --env-file .env ps
> ```

### 第 3 步：配置后端连接信息（唯一的"必须自己填"步骤）

打开文件 `backend/env/.env.dev`，把里面两处占位密码改成你在 `docker/.env` 里设的密码。
**具体怎么填见第 4 节，照着表格抄。**

### 第 4 步：安装后端依赖并启动后端

```bash
cd backend
uv sync                      # 首次：安装依赖（相当于 pnpm install）
uv run main.py run --env=dev # 启动后端
```

看到类似 `Uvicorn running on http://127.0.0.1:8001` 就成功了。

> - **第一次启动会自动建数据库表 + 写入基础数据**（菜单、账号等），你什么都不用做。
> - 开发模式下（DEBUG=True）**改代码会自动重启**，不用手动重启。
> - 验证：浏览器打开 <http://localhost:8001/docs>，能看到 Swagger 接口文档页面。

> 保持这个终端开着（Ctrl+C 可停止）。另开一个新终端做下一步。

### 第 5 步：启动管理后台前端

```bash
cd frontend/web
pnpm install   # 首次：安装依赖
pnpm dev       # 启动开发服务器
```

看到类似 `Local: http://localhost:5180/` 就成功了。

浏览器打开 **<http://localhost:5180/web>**，用账号登录：

| 账号 | 密码 |
|------|------|
| `super` | `123456` |
| `admin` | `123456` |
| `user` | `123456` |

> 登录后能看到菜单列表 = 前后端打通了 🎉
> 前端会自动把 `/api/v1` 的请求转发到后端 8001 端口（vite 代理），所以**不需要**处理跨域问题。

### 第 6 步（可选）：文档站 / 小程序

```bash
# 文档站（VitePress，和官方文档同源）
cd frontend/docs && pnpm install && pnpm dev

# 小程序（uni-app，需要微信开发者工具导入 dist/dev/mp-weixin）
cd frontend/app && pnpm install && pnpm dev:mp-weixin
```

---

## 3. 端口速查表（看到"端口被占用"就查这里）

| 服务 | 地址 | 端口 |
|------|------|------|
| 后端 API | <http://localhost:8001> | 8001 |
| 后端接口文档 (Swagger) | <http://localhost:8001/docs> | 8001 |
| 管理后台前端 | <http://localhost:5180/web> | 5180 |
| MySQL | localhost | 3306 |
| Redis | localhost | 6379 |
| 文档站 | 看终端输出的 Local 地址 | 5173 左右 |

---

## 4. 配置文件逐个填（重点看这节）

### 4.1 `docker/.env` —— 只读，别乱改

这个文件在**第一次部署时**定义 MySQL 和 Redis 的密码（也就是"给数据库设的密码"）。
你机器上这份已经填好了，**不要动它**。只看两个值，等会要用：

```
MYSQL_ROOT_PASSWORD=yyy   ← root 超级管理员的密码（日常用不到，只在手动建库/排查时用）
MYSQL_DATABASE=fastapiadmin   ← 数据库名字
MYSQL_USER=fastapiadmin       ← 数据库应用用户名
MYSQL_PASSWORD=xxx            ← 应用账号密码（等会要抄到 backend/env/.env.dev）
REDIS_PASSWORD=xxx            ← Redis 密码（等会要抄到 backend/env/.env.dev）
```

> **🧠 名词小教室：`MYSQL_ROOT_PASSWORD` vs `MYSQL_PASSWORD`**
>
> | 变量 | 代表谁 | 权限范围 | 后端用吗 |
> |------|--------|---------|---------|
> | `MYSQL_ROOT_PASSWORD` | **root 超级管理员** | 整个服务器，所有库都能管 | ❌ 不用 |
> | `MYSQL_USER` + `MYSQL_PASSWORD` | **应用账号** `fastapiadmin` | **只有** `fastapiadmin` 这一个库 | ✅ 就用它 |
>
> 比喻：root = 物业/房东，有整栋楼所有钥匙；fastapiadmin = 租户，只有自己办公室的钥匙。
> 这叫**最小权限原则**：项目代码只用应用账号，永远别用 root 跑业务。
>
> **两个都必须填**（docker-compose 强制，少一个容器起不来），但用途不同：
> - root 密码：MySQL 镜像首次初始化必需 + 你手动建库/排查时用（`docker exec mysql mysql -uroot -p`）。
> - 应用密码：后端连接数据库真正用的密码。
>
> **最容易抄错的地方**：填 `.env.dev` 的 `DATABASE_PASSWORD` 时抄的是 **`MYSQL_PASSWORD`（应用密码）**，不是 root 密码！抄成 root 密码会 Access denied。建议两个密码设成不同的。

> 注意：这里改了密码，已经创建的容器不会自动生效，需要重建容器。新手阶段**不要改它**。

### 4.2 `backend/env/.env.dev` —— 必须填（唯一要动脑的文件）

> 这个文件已被 git 忽略（不会提交到仓库），所以放心改。

打开文件，对照下表逐行检查。**打 ✅ 的必须确认，其他保持默认就行：**

| 配置项 | 填什么 | 示例/说明 |
|--------|--------|-----------|
| `ENVIRONMENT` | `dev` | 保持 dev，别改 prod |
| `DEBUG` | `True` | True = 改代码自动重载 + 报错信息详细，开发必开 |
| `DATABASE_TYPE` | `mysql` | 数据库类型（想零配置体验可临时改 `sqlite`，见 5.3） |
| `DATABASE_HOST` | `localhost` | MySQL 在本机 Docker 里，所以是 localhost |
| `DATABASE_PORT` | `3306` | 和 `docker/.env` 的 `MYSQL_PORT` 一致（默认 3306） |
| `DATABASE_USER` | `fastapiadmin` ✅ | **必须** = `docker/.env` 的 `MYSQL_USER` |
| `DATABASE_PASSWORD` | 你的 MySQL 密码 ✅ | **必须** = `docker/.env` 的 `MYSQL_PASSWORD` |
| `DATABASE_NAME` | `fastapiadmin` ✅ | **必须** = `docker/.env` 的 `MYSQL_DATABASE` |
| `REDIS_HOST` | `localhost` | Redis 在本机 Docker 里 |
| `REDIS_PORT` | `6379` | 和 `docker/.env` 的 `REDIS_PORT` 一致 |
| `REDIS_PASSWORD` | 你的 Redis 密码 ✅ | **必须** = `docker/.env` 的 `REDIS_PASSWORD` |
| `REDIS_DB_NAME` | `1` | 保持默认 |
| `OPENAI_API_KEY` | 你的 key 或留空 | 只有用 AI 对话功能才需要，留空不影响登录 |
| `SERVER_PORT` | `8001` | 后端端口，保持默认 |

**一句话总结：** 把 `docker/.env` 里的 `MYSQL_USER`、`MYSQL_PASSWORD`、`MYSQL_DATABASE`、`REDIS_PASSWORD` 四个值，抄到 `.env.dev` 对应位置即可。其余全部保持默认。

> **🧠 名词小教室：`DATABASE_USER` vs `DATABASE_NAME`**
>
> 把 MySQL 服务器想成一栋大楼：
>
> | 配置项 | 是什么 | 比喻 |
> |--------|--------|------|
> | `DATABASE_HOST`/`PORT` | 大楼地址 | 哪栋楼 |
> | `DATABASE_USER` | **你是谁** | 门禁卡/工号（身份） |
> | `DATABASE_PASSWORD` | 门禁卡密码 | 验证身份 |
> | `DATABASE_NAME` | **你去哪个房间** | 进楼后去哪个房间（目标） |
>
> - `USER` = 身份（我是谁），一个用户可被授权访问多个库。
> - `NAME` = 目标（我进哪个库），**后端只往这个库里建表和写数据**——它是"我的数据到底进哪"的最终决定者。
> - 一个 MySQL 服务器（大楼）里可以同时有很多个数据库（房间），互不相干。
>
> 查看自己服务器上有哪些库（前 4 个是 MySQL 自带的系统库，每个 MySQL 都有，永远别碰）：
> ```bash
> docker exec mysql mysql -uroot -p -e "SHOW DATABASES;"
> ```
> 你的机器上目前只有 `fastapiadmin` 一个真正的业务库。

### 4.3 `frontend/web/.env` 和 `.env.development` —— 通常不用动

这俩文件**项目已经帮你配好了默认值**（且 `.env.development` 已被 git 忽略）。看一眼知道是干嘛的即可：

- `.env`：通用配置。`VITE_PORT=5180`（前端端口）、`VITE_APP_BASE_API=/api/v1`（接口前缀）、`VITE_BASE_URL=/web`（访问路径，所以是 5180/web）。
- `.env.development`：开发环境专用。`VITE_API_BASE_URL=http://127.0.0.1:8001` 表示**前端把请求转发给本机 8001 的后端**。只要后端在 8001，就不用动。

> 前端唯一可能遇到的情况：如果哪天你改了后端端口，要把这里也改成一样。

---

## 5. 常见问题排查（新手必看）

### 5.1 后端启动报"连接数据库失败 / Access denied"

**99% 是密码不一致**。回去核对第 4.2 节：`backend/env/.env.dev` 里的 `DATABASE_PASSWORD` / `REDIS_PASSWORD`
是否和 `docker/.env` 里的完全一致（包括别多空格）。

### 5.2 后端启动报"连不上 Redis"

Redis 容器没起。执行第 2 步：`cd docker && docker compose --env-file .env up -d redis`，
然后用 `docker compose --env-file .env ps` 确认是 `Up` 状态。

### 5.3 我连 Docker 都不想用，能不能最简单跑起来？

能，用 SQLite 零配置。把 `backend/env/.env.dev` 改成：

```
DATABASE_TYPE = sqlite
# DATABASE_HOST / PORT / USER / PASSWORD 这几行删掉或注释掉即可
```

但 Redis 是登录/缓存必需的，所以最简单方案还是**只用 Docker 起一个 Redis**（`up -d redis`）。
想玩完整功能，MySQL 还是建议照第 2 步起。

### 5.4 前端页面能开，但登录报错 / 接口 404 / 网络错误

1. 确认后端终端还开着、没报错（后端挂了前端所有请求都会失败）。
2. 确认后端是 8001 端口、前端 `.env.development` 指向 8001（默认就是）。
3. 浏览器 F12 → Network 看请求，如果请求走的是 `http://localhost:8001/api/v1/...` 而 8001 没响应 → 后端没起来。

### 5.5 端口被占用（8001 / 5180 / 3306 等）

```bash
# 查谁占了端口（Linux/macOS）
lsof -i :8001
# 或 Windows: netstat -ano | findstr 8001
```

后端端口在 `backend/env/.env.dev` 的 `SERVER_PORT` 改；前端端口在 `frontend/web/.env` 的 `VITE_PORT` 改。

### 5.6 改后端代码没生效？

开发模式下改文件会自动重载（终端会打印 `Reloading...`）。如果没生效：
确认 `DEBUG = True`；如果是**新增了模块/路由**，需要手动重启后端（自动重载不处理新发现的模块）。

### 5.7 改了数据库表结构怎么办？

日常开发不用管（首次启动自动建表）。只有**你改了后端模型代码**才需要：

```bash
cd backend
uv run main.py revision -m "说明"   # 生成迁移文件
uv run main.py upgrade --env=dev     # 应用到数据库
```

---

## 6. 常用命令速查卡（复制粘贴用）

```bash
# ── 启动/停止基础设施 ──────────────────────────────
cd docker
docker compose --env-file .env up -d mysql redis   # 启动
docker compose --env-file .env ps                   # 查看状态
docker compose --env-file .env down                 # 停止（数据保留）

# ── 后端 ──────────────────────────────────────────
cd backend
uv sync                        # 装依赖（首次 / 依赖更新后）
uv run main.py run --env=dev   # 启动（热重载）
uv run pytest                  # 跑后端测试
uv run ruff check              # 代码规范检查

# ── 前端管理后台 ───────────────────────────────────
cd frontend/web
pnpm install    # 装依赖（首次）
pnpm dev        # 启动开发服务器
pnpm ts:check   # TypeScript 类型检查（改完代码跑一下）
pnpm lint       # 代码规范检查
pnpm build      # 构建生产包
```

---

## 7. 每日开发工作流（跑起来之后每天怎么用）

```bash
# 1. 电脑开机后，把基础设施拉起来（第 2 步那条命令）
cd docker && docker compose --env-file .env up -d mysql redis

# 2. 开终端 A：启动后端
cd backend && uv run main.py run --env=dev

# 3. 开终端 B：启动前端
cd frontend/web && pnpm dev

# 4. 浏览器打开 http://localhost:5180/web ，用 super/123456 登录
```

> 之后：改前端代码 → 浏览器自动刷新；改后端代码 → 终端 A 自动重载。
> 关电脑时 Ctrl+C 停掉两个终端即可（Docker 容器下次开机再起）。

---

## 8. 同时开发多个 FastapiAdmin 项目（隔离方案）

FastapiAdmin 的**默认值全部固定**：后端 8001、前端 5180、库名 `fastapiadmin`、Redis 库号 1。两个项目同时跑就会互相抢。

**核心思想：给每个项目发一套"独一无二的四件套"——后端端口 + 前端端口 + 数据库名 + Redis 库号，每个项目必须不同。**

**推荐方案：一个共享 MySQL + 一个共享 Redis，每个项目用独立的"数据库"和"库号"**（最省资源、数据完全隔离）。

### 8.1 分配示例（项目 A 是现有项目，B 是新项目）

| 项目 | 文件夹 | 后端端口 | 前端端口 | 数据库(用户) | Redis 库号 |
|------|--------|---------|---------|-------------|-----------|
| A（现有） | `~/coding/aiyun-base` | 8001 | 5180 | `fastapiadmin` | 1 |
| B（新项目） | `~/coding/aiyun-project-b` | **8002** | **5181** | `fastapiadmin_b` | **2** |

### 8.2 给项目 B 建库（在共享 MySQL 里建独立库 + 独立用户）

```bash
docker exec mysql mysql -uroot -p -e "
CREATE DATABASE fastapiadmin_b;
CREATE USER 'fastapiadmin_b'@'%' IDENTIFIED BY '你自己设的密码';
GRANT ALL ON fastapiadmin_b.* TO 'fastapiadmin_b'@'%';
"
```

### 8.3 改项目 B 的三个配置文件（A 完全不动）

**`backend/env/.env.dev`：**

```
SERVER_PORT = 8002          # 后端端口，避开 A 的 8001
DATABASE_USER = fastapiadmin_b
DATABASE_PASSWORD = 你自己设的密码
DATABASE_NAME = fastapiadmin_b
REDIS_DB_NAME = 2           # Redis 库号，避开 A 的 1
```

**`frontend/web/.env`：**

```
VITE_PORT = 5181            # 前端端口，避开 A 的 5180
```

**`frontend/web/.env.development`：**

```
VITE_API_BASE_URL = http://127.0.0.1:8002      # 代理指向 B 自己的后端
VITE_APP_WS_ENDPOINT = ws://localhost:8002     # AI 对话 WebSocket 也指 B
```

然后**在各自文件夹里分别启动**（各自有独立的 .venv 和 node_modules，互不干扰）：

```bash
# 项目 A（终端 1+2）
cd ~/coding/aiyun-base/backend && uv run main.py run --env=dev
cd ~/coding/aiyun-base/frontend/web && pnpm dev        # → localhost:5180/web

# 项目 B（终端 3+4）
cd ~/coding/aiyun-project-b/backend && uv run main.py run --env=dev
cd ~/coding/aiyun-project-b/frontend/web && pnpm dev   # → localhost:5181/web
```

### 8.4 三个容易犯的错

1. **两个项目共用同一个数据库** ❌ —— 两个项目建的**表一模一样**（sys_user、sys_menu…），共用会互相覆盖、数据错乱。必须各用各的库。
2. **共用 Redis 但库号相同** ❌ —— token、字典缓存会互相串。库号不同就完全隔离。
3. **前端代理指错端口** ❌ —— B 的前端必须指向 8002，指到 8001 就连到 A 的后端了。

### 8.5 什么时候才需要"每项目一个容器"

只有两种情况：两个项目需要**不同版本的 MySQL/Redis**，或想要**服务器级完全隔离**（不同 root 密码、不同配置）。平时共享实例 + 独立库/库号完全够用。

> 补充：如果"两个项目"其实是**同一个仓库的两个分支**，用 `git worktree add ~/coding/aiyun-project-b dev` 检出到独立文件夹即可，隔离规则完全一样。

---

## 附：你机器当前的准备情况（2025-09-14 检查）

| 项目 | 状态 | 说明 |
|------|------|------|
| uv / Python / Node / pnpm / Docker | ✅ 已装 | 版本都满足要求 |
| MySQL 容器 | ✅ 已在运行 | healthy，密码已存在 `docker/.env` |
| Redis 容器 | ❌ 未启动 | 需执行第 2 步启动 |
| `backend/env/.env.dev` | ⚠️ 还是占位密码 | 需按第 4.2 节填写（和 `docker/.env` 对齐） |
| 后端依赖 (.venv) | ❌ 未安装 | 执行第 4 步 `uv sync` |
| 前端依赖 (node_modules) | ❌ 未安装 | 执行第 5 步 `pnpm install` |

> 也就是说：你只需做 **第 2 步（起 Redis）→ 第 4.2 节（填 .env.dev）→ 第 4 步（uv sync + 启动）→ 第 5 步（pnpm）** 就能全部跑通。
