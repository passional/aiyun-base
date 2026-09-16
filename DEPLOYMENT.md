# 内网服务器部署文档

> 适用场景：**公司内网服务器，只能内网访问，无域名，使用自签证书提供 HTTPS。**
>
> 部署方式：本地构建 → 上传项目目录到服务器 → 服务器上执行 `deploy.sh` 完成构建与启动。
> 服务器**不需要**联网拉代码，也**不需要**装 Node.js（前端在本地构建好再上传）。

---

## 1. 架构与端口

```
                    内网用户浏览器
                          │  https://<服务器IP>:8433
                          ▼
              ┌───────────────────────┐
              │ Windows 宿主（如有）    │   ← IIS 占用 80/443，需做端口转发
              │ 见 2.5 节              │      把 8433 转发进虚拟机
              └───────────┬───────────┘
                          ▼
              ┌───────────────────────┐
              │ nginx 容器 (:443 TLS)  │   ← 唯一对外暴露的端口
              │   /web   → 前端静态文件 │
              │   /api/v1 → 反代后端    │
              │   自签证书（含 IP SAN） │
              └───────────┬───────────┘
                          │ app_network（容器内网）
                          ▼
              ┌───────────────────────┐
              │  backend 容器 (:8001)  │
              └────┬─────────────┬────┘
                   ▼             ▼
            ┌──────────┐  ┌──────────┐
            │  mysql   │  │  redis   │   ← 端口只绑 127.0.0.1，内网不可达
            └──────────┘  └──────────┘
```

| 服务 | 容器内端口 | 宿主机端口 | 说明 |
|------|-----------|-----------|------|
| nginx | 443（TLS） | `${BIND_IP}:${HTTPS_PORT}`（默认 `0.0.0.0:8433`） | **唯一对外暴露的端口**。设 `BIND_IP` 可限制到内网 IP，见第 2 节 |
| backend | 8001 | `127.0.0.1:${BACKEND_PORT}` | 仅回环。外部请求统一走 nginx（限流 / 安全头） |
| mysql | 3306 | `127.0.0.1:${MYSQL_PORT}` | 仅回环，供服务器上调试用 |
| redis | 6379 | `127.0.0.1:${REDIS_PORT}` | 仅回环 |

> 三个内部服务都绑定在 `127.0.0.1`，容器之间通过 `app_network` 直连不受影响。
> 若需要从内网其他机器直连后端调试，把 `docker-compose.yaml` 中 backend 的
> 端口映射改回 `"${BACKEND_PORT:-8001}:8001"` 后重新 `./deploy.sh`。

> **为什么用 8433 而不是 443**：宿主机上已有 IIS 占用 80/443（见 2.5 节）。
> 容器内 nginx **始终监听标准端口 443**，`HTTPS_PORT` 只决定宿主机侧的映射端口。

### 访问地址

| 用途 | 地址 |
|------|------|
| 系统首页 | `https://<服务器IP>:8433/web`（访问 `https://<服务器IP>:8433/` 会 302 跳转过去） |
| Swagger 接口文档 | `https://<服务器IP>:8433/api/v1/docs` |
| ReDoc 接口文档 | `https://<服务器IP>:8433/api/v1/redoc` |
| 健康检查 | `https://<服务器IP>:8433/api/v1/monitor/health/check` |
| 默认账号 | `admin` / `123456`（**首次登录后请立即修改**） |

> **首次访问会有证书告警**：因为是自签证书，浏览器会提示「您的连接不是私密连接」。
> 点「高级」→「继续前往」即可，详见第 10 节。

---

## 2. 服务器前置要求

在服务器上执行以下命令确认：

```bash
# 1. 架构必须是 x86_64（docker-compose.yaml 中写死了 platform: linux/amd64，
#    ARM 架构如鲲鹏/飞腾会走模拟，极慢且可能起不来）
uname -m

# 2. Docker 与 Compose 版本（需要 Docker >= 20.10，Compose v2）
docker --version
docker compose version

# 3. 磁盘空间（镜像 + 数据库，建议预留 20GB 以上）
df -h /opt

# 4. HTTPS_PORT 不能被占用（默认 8433）
sudo ss -lntp | grep ':8433 '
```

### 2.1 Compose 必须是 v2，不能用 docker-compose v1

新装的服务器上经常只装了 `docker-compose`（Python 写的 v1）。**它跑不了本项目**：

| 特性 | `docker compose`（v2） | `docker-compose`（v1） |
|------|----------------------|----------------------|
| `depends_on.condition: service_healthy` | 支持 | 不支持 |
| `deploy.resources.limits.cpus` | 支持 | **静默忽略** |
| 状态 | 维护中 | 已 EOL |

后果是 backend 不等 MySQL 健康就启动、CPU 限制悄悄失效 —— 都要到运行期才暴露。

**判断当前是哪个：**

```bash
docker compose version        # v2 → 输出 "Docker Compose version v2.x.x"
docker-compose --version      # v1 → 输出 "docker-compose version 1.x.x"
```

只要第一条报 `docker: 'compose' is not a docker command.`，就说明 v2 没装。

**安装方式（任选其一）：**

```bash
# 方式一：已配置 docker-ce 软件源时（最省事）
yum install -y docker-compose-plugin

# 方式二：手动放二进制（Docker >= 19.03 才有 cli-plugins 机制）
mkdir -p /usr/local/lib/docker/cli-plugins
curl -SL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64 \
     -o /usr/local/lib/docker/cli-plugins/docker-compose
chmod +x /usr/local/lib/docker/cli-plugins/docker-compose

# 方式三：服务器连不上 GitHub 时，在有网的机器上下好二进制再传过去
#   scp -P 2210 docker-compose-linux-x86_64 root@<服务器>:/usr/local/lib/docker/cli-plugins/docker-compose
```

装完用 `docker compose version` 验证。`deploy.sh` 会在启动前检查这一项，
不满足就直接报错停下，不会走到一半才发现。

> 如果 `docker --version` 显示 **1.13.x**（CentOS 7 基础源里的老版本），
> 它没有 `cli-plugins` 机制，方式二无效 —— 需要先升级 Docker Engine 本身。

如果 Docker 未安装，参考官方文档安装 Docker Engine + Compose 插件。

### 网络隔离

**先明确一点：本方案的主要隔离手段是端口绑定，不是防火墙。**

`docker-compose.yaml` 已把 backend / MySQL / Redis 绑定到 `127.0.0.1`。
绑定回环地址后，内核不接受目的地址不是 `127.0.0.1` 的连接，这三个服务在
内网上本来就不可达。nginx 是唯一监听网络接口的服务，且只有 443 端口。

#### Docker 与 ufw / firewalld 的一个常见误解

Docker 发布端口是在 iptables 的 `nat` 表做 DNAT，包到达 `filter` 表时已经是
**FORWARD** 流量，而 ufw / firewalld 默认只过滤 **INPUT** 链。因此：

| 命令 | 对 Docker 发布的端口 |
|------|---------------------|
| `ufw allow 8433/tcp` | 不生效（空操作） |
| `ufw deny 8001` | 不生效，拦不住 |

**不要指望 ufw 拦住 Docker 端口。** 要拦必须用 `DOCKER-USER` 链（见下）。

#### 防火墙真正有用的地方

1. **保护服务器上的其他服务**：SSH、系统服务等走 INPUT 链，防火墙对它们有效
2. **兜底**：以后若有人把端口映射改回 `3306:3306`，规则还在
3. **公司合规要求**

#### 严格做到「只能内网访问」

`ufw allow 8433` 的语义是「对任何能路由到本机的地址放行」，**不等于**「只对内网」。
服务器若有 VPN、跳板机或第二张网卡，范围就超出预期。

**做法 A（推荐）：把 nginx 也绑到内网 IP**

在 `docker/.env` 中设置：

```env
BIND_IP=192.168.10.15     # 换成服务器内网 IP
```

然后重新执行 `./deploy.sh`。Docker 只为这个 IP 做转发，服务器其他 IP 上
根本连不上。规则跟着 compose 文件走，重启不丢失，也不依赖防火墙配置。

**做法 B：用 DOCKER-USER 链限制来源网段**

```bash
# 假设内网网段是 192.168.10.0/24
sudo iptables -I DOCKER-USER -p tcp --dport 8433 ! -s 192.168.10.0/24 -j DROP

# 重启后保留需额外安装
sudo apt install iptables-persistent && sudo netfilter-persistent save
```

#### 建议同时限制 SSH

SSH 是宿主机进程，走 INPUT 链，**ufw 对它有效**：

```bash
# 注意端口号：本服务器 SSH 改到了 2210，不是默认的 22
sudo ufw allow from 192.168.10.0/24 to any port 2210 proto tcp
sudo ufw enable
```

#### 验证

```bash
# 1. 确认只有 nginx 监听在网络接口上（期望只看到 :8433）
sudo ss -lntp | grep -v '127.0.0.1\|\[::1\]'

# 2. 确认 backend / MySQL / Redis 都只在回环（期望全部是 127.0.0.1）
sudo ss -lntp | grep -E ':(8001|3306|6379)'

# 3. 从内网另一台机器验证可达性（自签证书，必须加 -k）
curl -kI https://<服务器IP>:8433/web/     # 期望 200 或 302
```

### 2.5 如果服务器是虚拟机（宿主上有 IIS）

有些部署环境里，「服务器」其实是一台物理机上的 Linux 虚拟机：

- **Windows 宿主**占用了内网 IP，80/443 被 IIS 使用
- **Linux 虚拟机**在 VMware/VirtualBox 的 NAT 网络里，IP 形如 `192.168.115.x`
- 这个 NAT 地址内网其他机器**路由不到**，必须由宿主做端口转发

判断方法（在虚拟机里执行）：

```bash
ip route | grep default
```

- 网关形如 `192.168.x.2` 且和宿主同段 → 很可能是 NAT 模式，需要下面的转发
- 网关就是内网网关（如 `192.168.10.1`）→ 虚拟机已桥接，跳过本节

**在 Windows 宿主上以管理员身份执行**（`cmd` 或 PowerShell）：

```cmd
:: 1. 把宿主的 8433 转发到虚拟机的 8433
netsh interface portproxy add v4tov4 listenport=8433 listenaddress=192.168.10.15 connectport=8433 connectaddress=192.168.115.129

:: 2. 放行防火墙（不加这条，转发规则依然不通）
netsh advfirewall firewall add rule name="aiyun-base-8433" dir=in action=allow protocol=TCP localport=8433

:: 3. 查看已有规则
netsh interface portproxy show all
```

要点：

- `connectaddress` 填**虚拟机**的地址（用 `ip route get 1.1.1.1` 在虚拟机里查）
- 虚拟机必须用**固定 IP**。VMware 的 NAT DHCP 会换地址，换了之后转发就失效
- `portproxy` 是 TCP 层转发，WebSocket 可以正常穿透，不需要额外配置
- 删除规则：`netsh interface portproxy delete v4tov4 listenport=8433 listenaddress=192.168.10.15`

**验证**：在 Windows 宿主上先确认转发本身通

```cmd
curl -k https://192.168.115.129:8433/web/     :: 直连虚拟机
curl -k https://192.168.10.15:8433/web/       :: 走转发
```

两条都应返回 200/302。第二条不通就检查防火墙规则。

---

## 3. 本地准备

### 3.1 修改前端生产环境变量（必做）

`frontend/web/.env.production` 里有一个**必须改**的值：

```env
VITE_APP_WS_ENDPOINT = wss://192.168.10.15:8433
```

这个地址用于 WebSocket 连接，AI 对话、文件传输进度、渠道评估进度都依赖它。

填法规则：

| 部署方式 | 协议 | 示例 |
|---------|------|------|
| HTTPS（8433，本方案） | **`wss://`** | `wss://192.168.10.15:8433` |
| 纯 HTTP | `ws://` | `ws://192.168.10.15:8433` |

**协议必须和部署方式一致，且端口号不能省**（不是 443 就必须写出来）。
写错的典型表现是：页面能打开，但 AI 对话发不出消息、进度条一直不动。

> 该文件已被 `.gitignore` 排除，不会提交到仓库，只用于本地构建。

### 3.2 构建前端

```bash
cd frontend/web
pnpm install          # 或 npm install
pnpm build
```

构建产物在 `frontend/web/dist/`，把它复制到 nginx 的挂载目录：

```bash
cd /home/zzhou/coding/aiyun-base
rm -rf docker/nginx/web/dist
cp -r frontend/web/dist docker/nginx/web/dist
```

> **关于构建模式**：`pnpm build` 等价于 `vite build`，走 Vite 默认的 `production`
> 模式，读取的是 `.env.production`，生产优化（terser 压缩、gzip/brotli 预压缩、
> 剔除 vue-devtools）随之生效。
>
> 产物中每个 JS/CSS 都会额外生成 `.gz` 和 `.br` 文件。当前 nginx 用的是动态
> `gzip on`，不会读取这些预压缩文件，它们只是让上传体积变大（约多 5MB），
> 不影响功能。若想让 nginx 直接使用，在 `nginx.conf` 的 `http` 块中加一行
> `gzip_static on;` 即可（brotli 需要额外编译模块，官方镜像不含）。

### 3.3 检查上传内容

上传范围由项目根目录的 **`.deployignore`** 决定，`scripts/upload.sh` 会读取它。
不用手工记排除项，但值得知道为什么排除这些：

| 排除项 | 原因 |
|--------|------|
| `docker/mysql/data/`、`docker/redis/data/` | 本地数据文件，属主是容器用户，传上去必踩权限坑 |
| `docker/.env`、`backend/env/.env.prod` | 只存在于本地、含密码和密钥，服务器上单独生成 |
| `frontend/` | 构建产物已在 `docker/nginx/web/dist/`，服务器不构建前端 |
| `docker/backend/static/upload/` | 服务器上的用户上传文件。排除它 = 让 `--delete` 保护它不被清空 |
| `node_modules/`、`.venv/`、`backend/logs/`、`.git/` | 依赖与运行时产物 |

**必须上传，但不在 git 里**（脚本会强制检查，缺任何一个直接报错停下）：

- `docker/nginx/web/dist/` —— 前端构建产物（`.gitignore` 第 57 行排除）
- `docker/nginx/ssl/server.pem` + `server.key` —— TLS 证书（`.gitignore` 第 58 行排除）

漏传的后果很隐蔽：前者让页面打不开，后者让 nginx 直接起不来。

**必须上传，已在 git 里**：

- `backend/data/ip2region/ip2region_v4.xdb` —— IP 归属地离线库（约 11MB）
  缺失时功能降级为「未解析」，不会报错，但登录日志里的归属地会失效

排除之后，总传输量约 **41 MB / 671 个文件**（rsync 实传量，压缩后约 19 MB）。

---

## 4. 上传到服务器

上传已经封装成脚本，在**本地**项目根目录执行：

```bash
./scripts/upload.sh --dry-run    # 先预览会传什么（强烈建议第一次这样做）
./scripts/upload.sh              # 实际传输
```

默认目标是 `root@192.168.10.15:2210 → /opt/aiyun-base`。换服务器时用环境变量覆盖，
不用改脚本：

```bash
DEPLOY_HOST=10.0.0.5 DEPLOY_PORT=22 DEPLOY_USER=deploy \
DEPLOY_PATH=/srv/aiyun-base ./scripts/upload.sh
```

| 参数 | 作用 |
|------|------|
| （无） | 增量同步。重复执行只传变化的部分，很快 |
| `--dry-run` | 只列出会传什么，不实际传输 |
| `--delete` | 同步并删除服务器上的多余文件 |
| `--tar` | 服务器没装 rsync 时改用 tar 经 ssh 流式传输（全量，不支持 `--delete`） |

脚本在传输前会做三项检查，任何一项不过就直接停下：

1. ssh 端口（默认 2210）是否可达
2. `docker/nginx/ssl/server.pem` 与 `server.key` 是否存在
3. `docker/nginx/web/dist/index.html` 是否存在

> **关于 `--delete` 的安全性**：`.deployignore` 里排除了 `docker/.env`、
> `docker/mysql/data/`、`docker/backend/static/upload/` 等，rsync 的 `--delete`
> 会**同时保护**这些路径，不会删除服务器上的对应文件。这一点已实测验证。
>
> 但 `--delete` 仍然会删除「服务器上有、本地没有、且不在排除清单里」的文件。
> 首次部署不必加；确定服务器上没有需要保留的额外文件后再用。

### 如果服务器没装 rsync

这是内网新装的服务器上很常见的情况。症状是 rsync 跑一半报：

```
bash: rsync: command not found
rsync error: error in rsync protocol data stream (code 12)
```

**两个办法，任选其一：**

**办法一 —— 在服务器上装 rsync（推荐，之后就能用增量同步了）**

```bash
yum install -y rsync     # CentOS / RHEL / Rocky / Alma
apt install -y rsync     # Debian / Ubuntu
```

**办法二 —— 改用 tar 通路，不用装任何东西**

```bash
./scripts/upload.sh --tar --dry-run   # 先在本地预览打包内容
./scripts/upload.sh --tar             # 流式传输到服务器
```

`--tar` 会把打包结果直接经 ssh 管道送过去，再从远端解开，本地不留临时文件。
代价是**全量传输**、不支持 `--delete`，重复部署比 rsync 慢。

> **tar 的排除格式有个坑**：GNU tar 的 `--exclude-from` **不支持**结尾斜杠的目录
> 模式。如果照抄 rsync 的写法写成 `frontend/`，tar 会**完全忽略**这条规则，
> 把整个 `frontend/`（含 `node_modules`）打进包里 —— 体积从 19 MB 变成 376 MB。
>
> 所以本项目的 `.deployignore` 刻意**统一不写结尾斜杠**（写 `.git` 而不是 `.git/`），
> 让同一份清单对两个工具都生效。已实测：两条通路都是 **671 个条目**，
> `.git` / `frontend` / `.venv` / `docker/mysql/data` 均被排除，
> `docker/nginx/web/dist/` 与 `docker/nginx/ssl/` 正常保留。

### 如果服务器不能直连

要过堡垒机 / 跳板机时，`--tar` 也可以拆成两步手工做：

```bash
tar --exclude-from=.deployignore -czf /tmp/aiyun-base.tar.gz ./
scp -P 2210 /tmp/aiyun-base.tar.gz root@192.168.10.15:/opt/
# 服务器上：
mkdir -p /opt/aiyun-base && tar -xzf /opt/aiyun-base.tar.gz -C /opt/aiyun-base
```

---

## 5. 服务器首次部署

以下命令全部在**服务器**上、项目根目录执行。

### 5.1 配置 `docker/.env`

```bash
cd /opt/aiyun-base
cp docker/.env.example docker/.env
chmod 600 docker/.env
vi docker/.env
```

必须填写（生成强密码：`openssl rand -base64 24`）：

```env
MYSQL_ROOT_PASSWORD=<强密码>
MYSQL_PASSWORD=<强密码>
REDIS_PASSWORD=<强密码>

MYSQL_DATABASE=fastapiadmin
MYSQL_USER=fastapiadmin
HTTPS_PORT=8433
BACKEND_PORT=8001
DEPLOY_ENV=prod
LOGGER_LEVEL=INFO
```

> 如果服务器是虚拟机、且宿主上还有别的服务占端口，`HTTPS_PORT` 可以改成
> 宿主上任意空闲端口（例如 8433 被占时改用 9443）。改完记得同步更新
> `frontend/web/.env.production` 的 `VITE_APP_WS_ENDPOINT` 并重新构建前端。

### 5.2 配置 `backend/env/.env.prod`

后端以 `--env=prod` 启动，读取这个文件。**它会被打进后端镜像，所以必须在构建前配好。**

```bash
cp backend/env/.env.prod.example backend/env/.env.prod
chmod 600 backend/env/.env.prod
vi backend/env/.env.prod
```

必须确认的三项（其余按注释填）：

| 配置项 | 值 | 原因 |
|--------|-----|------|
| `HTTPS_REDIRECT_ENABLE` | `True`（保持默认） | nginx 会带 `X-Forwarded-Proto: https` 转发，因此不会触发跳转。**仅当改为纯 HTTP 部署时才设为 `False`**，否则所有请求会被 301 到一个不存在的 https 端口 |
| `ALLOWED_HOSTS` | `["*"]` | 默认只允许 `service.fastapiadmin.com`，用 IP 访问会返回 **400 Invalid host header**。必须是 JSON 数组格式 |
| `SECRET_KEY` | `openssl rand -hex 32` 的结果 | 不修改会使用代码里的开发默认值，等同于没有认证安全 |

补充说明：

- `DATABASE_*` / `REDIS_*` 在 compose 部署下会被 `docker/.env` 的同名变量覆盖，
  **改数据库密码要改 `docker/.env`**，改这里不生效。
- `DATA_ENCRYPTION_KEY` 留空时会由 `SECRET_KEY` 派生。一旦设置了就不要随意变更，
  否则已加密的历史数据无法解密。

### 5.3 执行部署

```bash
./deploy.sh
```

脚本会依次执行：加载环境变量 → 检查依赖并创建目录 → 校验代码完整性 →
构建镜像 → 启动容器 → 验证服务 → 输出日志 → 清理构建缓存。

首次构建需要下载 Python 基础镜像和依赖，视网络情况可能需要几分钟。

### 5.4 自签证书

证书文件在 `docker/nginx/ssl/` 下，随项目一起上传（已被 `.gitignore` 排除，不会进仓库）。
仓库里预置了一套覆盖 `192.168.10.15` / `192.168.115.129` 的证书，**如果你部署的
服务器 IP 不同，必须重新生成**，否则浏览器除了「不受信任」还会多报一个域名不匹配：

```bash
cd /opt/aiyun-base

# 把 IP 换成实际值（多个 IP 用逗号分隔）
IP=192.168.10.15

openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
  -keyout docker/nginx/ssl/server.key \
  -out docker/nginx/ssl/server.pem \
  -subj "/C=CN/O=aiyun-base/CN=$IP" \
  -addext "subjectAltName=IP:$IP,DNS:localhost"

# 核对结果，Subject Alternative Name 里必须包含你访问时用的那个 IP
openssl x509 -in docker/nginx/ssl/server.pem -noout -subject -dates
openssl x509 -in docker/nginx/ssl/server.pem -noout -text | grep -A1 "Subject Alternative Name"

cd docker && docker compose restart nginx
```

生成了新的证书后重跑 `curl -k https://127.0.0.1:8433/web/` 验证。

> **不要用 `-ext subjectAltName`**：那是 OpenSSL 3.x 才有的选项，CentOS 7 自带的
> 1.0.2 会直接报 `unknown option -ext`。上面用的 `-text | grep` 在新旧版本上都能跑。
>
> `-addext` 同样需要 OpenSSL 1.1.1 以上。**如果服务器上的 openssl 版本过低，
> 建议在本地生成证书再上传** —— 本项目的证书本来就走上传流程（见第 3.3 节
> 「必须上传，但不在 git 里」），本地环境一般版本较新，不用为这个去升级服务器的
> OpenSSL。

---

## 6. 部署验证

```bash
# 1. 容器状态：mysql / redis 应为 healthy，backend / nginx 应为 Up
cd /opt/aiyun-base/docker && docker compose ps

# 2. 或用脚本自带的检查
cd /opt/aiyun-base && ./deploy.sh verify

# 3. 前端静态页（-k 跳过自签证书校验）
curl -kI https://127.0.0.1:8433/web/

# 4. 后端 API —— 这条才是整条链路（nginx → backend → MySQL/Redis）的验证
curl -k https://127.0.0.1:8433/api/v1/monitor/health/check

# 5. 确认证书内容正确
echo | openssl s_client -connect 127.0.0.1:8433 2>/dev/null |
  openssl x509 -noout -text | grep -A1 "Subject Alternative Name"
```

> **不要用 `curl -i http://127.0.0.1:8001/...` 直接测后端。** 后端开了
> `HTTPS_REDIRECT_ENABLE=True`，收到纯 HTTP 请求时会返回 301 跳到自己并不提供的
> HTTPS 端口上；而且在容器刚重建完时还可能碰上 `Connection reset by peer`。
> 这个报错跟后端健康状况无关，只说明「不该绕过 nginx 直连」。走后端一律经
> nginx，也就是上面第 4 条。

然后在**内网的其他机器**上用浏览器打开 `https://<服务器IP>:8433/`，
会先弹出证书告警（点「高级」→「继续前往」），然后跳到登录页，
用 `admin` / `123456` 登录。

> 如果访问不通而服务器本机 `curl` 正常，问题多半在第 2.5 节的端口转发上。

数据库表和后端启动时会自动执行 `alembic upgrade head` 建表并写入种子数据，
**不需要手动导入 SQL**。

---

## 7. 日常运维

### 常用命令

```bash
cd /opt/aiyun-base

./deploy.sh logs      # 查看所有容器状态 + 最近 50 行日志
./deploy.sh restart   # 重启所有容器
./deploy.sh stop      # 停止所有容器（数据保留）
./deploy.sh start     # 启动所有容器
./deploy.sh verify    # 验证服务状态
./deploy.sh clean     # 清理悬空镜像和构建缓存（释放磁盘）
```

查看单个服务的实时日志：

```bash
cd docker
docker compose logs -f backend
docker compose logs -f nginx
docker compose logs -f mysql
```

### 更新版本

```bash
# 1. 本地：如有前端改动，重新构建并复制
cd frontend/web && pnpm build
rm -rf ../../docker/nginx/web/dist && cp -r dist ../../docker/nginx/web/dist

# 2. 本地：重新上传（命令同第 4 节）

# 3. 服务器：重新构建并重启
cd /opt/aiyun-base && ./deploy.sh
```

> 数据库数据存放在 `docker/mysql/data/`，是宿主机目录挂载，**重建容器不会丢数据**。
> 用户上传的文件存放在 `backend/static/upload/`，同样是宿主机目录挂载，也不会丢。

### 备份

```bash
cd /opt/aiyun-base/docker
set -a; source .env; set +a

# 数据库
docker exec mysql mysqldump -uroot -p"${MYSQL_ROOT_PASSWORD}" \
  --single-transaction --default-character-set=utf8mb4 \
  "${MYSQL_DATABASE}" | gzip > ~/backup-db-$(date +%F).sql.gz

# 用户上传的文件
tar -czf ~/backup-upload-$(date +%F).tar.gz -C /opt/aiyun-base/backend/static upload
```

建议用 crontab 每天定时执行，并把备份文件同步到其他机器。

### 恢复

```bash
cd /opt/aiyun-base/docker
set -a; source .env; set +a
gunzip -c ~/backup-db-2026-01-01.sql.gz | \
  docker exec -i mysql mysql -uroot -p"${MYSQL_ROOT_PASSWORD}" "${MYSQL_DATABASE}"
```

---

## 8. 常见问题排查

| 现象 | 原因与处理 |
|------|-----------|
| 访问 IP 返回 **400 Invalid host header** | `backend/env/.env.prod` 的 `ALLOWED_HOSTS` 没设成 `["*"]`（注意必须是 JSON 数组格式），改完执行 `./deploy.sh` 重建镜像 |
| 浏览器提示「连接不是私密连接」 | **正常现象**，自签证书必然如此。点「高级」→「继续前往」。见第 10 节 |
| 浏览器报 `NET::ERR_CERT_COMMON_NAME_INVALID` | 证书里的 IP 和实际访问的 IP 不一致。按第 5.4 节重新生成证书 |
| 从内网机器打不开，但服务器本机 `curl -k https://127.0.0.1:8433` 正常 | 虚拟机 NAT 网络没做端口转发。见第 2.5 节 |
| nginx 容器反复重启（`Restarting`） | 配置语法错误或证书缺失。`docker compose logs nginx` 看具体行号；或 `docker exec nginx nginx -t` |
| nginx 报 `cannot load certificate` | `docker/nginx/ssl/server.pem` / `server.key` 不存在。按第 5.4 节生成 |
| **502 Bad Gateway** | 后端没起来。`docker compose logs backend` 看报错，常见是 MySQL/Redis 连不上或 `SECRET_KEY` 等必填项缺失 |
| 页面能打开但接口全 502/404 | 检查 `docker/nginx/web/dist` 是否上传成功；接口路径必须是 `/api/v1/...` |
| 前端白屏 / 刷新 404 | `docker/nginx/web/dist` 目录不存在或内容为空，重新构建上传 |
| MySQL 起不来，日志报权限错误 | `sudo chown -R 999:999 docker/mysql/data && sudo chmod -R 755 docker/mysql/data` |
| HTTPS_PORT 冲突 | 改 `docker/.env` 的 `HTTPS_PORT`（如 9443），重新 `./deploy.sh`，访问地址变成 `https://<IP>:9443`；**同时要改 `VITE_APP_WS_ENDPOINT` 并重新构建前端** |
| AI 对话 / 进度条不更新 | `frontend/web/.env.production` 的 `VITE_APP_WS_ENDPOINT` 配错。HTTPS 部署必须是 `wss://<IP>:8433`，改完**重新构建前端并上传** |
| WebSocket 报 `Mixed Content` 被浏览器拦截 | 页面是 https 但 WS 用了 `ws://`。改成 `wss://` 后重新构建前端 |
| 登录后立刻掉线 | `SECRET_KEY` 变更导致已签发的 JWT 失效，重新登录即可；若持续掉线检查 Redis 是否正常 |
| 上传大文件失败 | nginx `client_max_body_size` 为 50m，如需调大改 `docker/nginx/nginx.conf` 后 `docker compose restart nginx` |

查看日志时优先看 `backend`，绝大多数业务问题都反映在那里。

---

## 9. 安全清单

部署完成后逐项确认：

- [ ] `docker/.env` 中三个密码均已改为强密码，文件权限 `600`
- [ ] `backend/env/.env.prod` 的 `SECRET_KEY` 已改为随机值，文件权限 `600`
- [ ] 已登录后台修改 `admin` 默认密码
- [ ] `sudo ss -lntp` 确认 backend / MySQL / Redis 都只监听 `127.0.0.1`（8001 / 3306 / 6379）
- [ ] 如需严格限制来源，已设置 `BIND_IP`（第 2 节做法 A）或配置了 `DOCKER-USER` 规则（做法 B）
- [ ] SSH 已限制到管理网段，且用的是实际端口 2210（第 2 节）
- [ ] 自签证书的 SAN 里包含用户实际访问的 IP（第 5.4 节）
- [ ] `backend/env/.env.prod` 和 `docker/.env` 未提交到 git 仓库
- [ ] `docker/nginx/ssl/server.key` 未提交到 git 仓库（`.gitignore` 已排除）
- [ ] 已配置数据库定时备份
- [ ] 已知悉：证书有效期 10 年，**到期前需重新生成并重启 nginx**（第 5.4 节）

---

## 10. 关于自签证书

本方案用 HTTPS（8433），属于**安全上下文**，浏览器的 `navigator.clipboard`、
Service Worker 等能力均可正常使用。

代价是每次访问都有证书告警，需要用户手动点一次「继续前往」。

### 为什么一定要用自签证书而不是纯 HTTP

| | HTTPS + 自签证书 | 纯 HTTP |
|---|---|---|
| 浏览器告警 | 有，首次需手动放行 | 无 |
| 「复制」按钮 | ✅ 正常 | ❌ 失效（非安全上下文） |
| 其他安全上下文能力 | ✅ 正常 | ❌ 失效 |
| 传输加密 | ✅（自签也加密） | ❌ 明文 |

### 消除告警的两种办法

1. **把证书导入客户端信任库**（推荐，一次配置长期有效）
   把 `docker/nginx/ssl/server.pem` 分发给使用者的电脑，导入到「受信任的根证书颁发机构」。
   - Windows：双击 `server.pem` → 安装证书 → 本地计算机 → 受信任的根证书颁发机构
   - macOS：钥匙串访问 → 导入 → 设为「始终信任」
   - Firefox：设置 → 隐私与安全 → 证书 → 查看证书 → 导入

   注意：导入的是**公钥** `server.pem`，**绝对不要**分发 `server.key`（那是私钥）。

2. **内网 DNS + 自建 CA**：给服务器分配内网域名，用企业 CA 签发证书。
   效果最好，但需要 IT 部门配合。

### 证书到期

仓库预置的证书有效期 **10 年**（到 2036-09）。到期前按第 5.4 节重新生成即可，
客户端重新导入一次公钥。

---

## 11. 附：切换部署形态

### 改成纯 HTTP 部署

如果不需要 TLS（例如内网已有其他加密手段），可以退回纯 HTTP：

1. `docker/nginx/nginx.conf`：把 `listen 443 ssl;` 改为 `listen 80;`，
   删除 `ssl_*` 相关行和 `http2 on;`。保留 `absolute_redirect off;`（仍然需要）。
2. `docker/docker-compose.yaml`：端口映射改为 `"${BIND_IP:-0.0.0.0}:${HTTP_PORT:-80}:80"`。
3. `docker/.env`：把 `HTTPS_PORT` 换成 `HTTP_PORT=80`。
4. `backend/env/.env.prod`：`HTTPS_REDIRECT_ENABLE = False`（**不改会导致页面打不开**）。
5. `frontend/web/.env.production`：`VITE_APP_WS_ENDPOINT` 改为 `ws://<IP>:<端口>`，
   重新构建前端 —— **不改会被浏览器当作 Mixed Content 拦截**。

### 恢复成公网 + 域名 + 正规证书

1. `docker/nginx/nginx.conf`：把 CA 签发的证书放到 `docker/nginx/ssl/`；
   如需强制跳转，再补一个 80 端口做 301 到 https 的 server 块。
2. `backend/env/.env.prod`：`ALLOWED_HOSTS` 改回实际域名（不再用 `["*"]`），
   `HTTPS_REDIRECT_ENABLE` 保持 `True`。
3. `docker/docker-compose.yaml`：按需增加 80 端口映射。
4. `frontend/web/.env.production`：`VITE_APP_WS_ENDPOINT` 改用 `wss://<域名>`，重新构建前端。
