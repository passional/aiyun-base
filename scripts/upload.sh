#!/usr/bin/env bash
#
# 把项目上传到内网服务器
#
# 用法（在项目根目录执行）：
#   ./scripts/upload.sh                 # 增量同步（推荐，重复执行很快）
#   ./scripts/upload.sh --dry-run       # 只预览会传什么，不实际传输
#   ./scripts/upload.sh --delete        # 同步并删除服务器上的多余文件
#   ./scripts/upload.sh --tar           # 服务器没装 rsync 时，改用 tar 经 ssh 流式传输
#
# 服务器地址可通过环境变量覆盖：
#   DEPLOY_HOST=192.168.10.15 \
#   DEPLOY_PORT=2210 \
#   DEPLOY_USER=root \
#   DEPLOY_PATH=/opt/aiyun-base \
#   ./scripts/upload.sh
#
# 排除清单见项目根目录的 .deployignore。
#
set -euo pipefail

# ─ 配置（环境变量可覆盖）──────────────────────────────────────
DEPLOY_HOST="${DEPLOY_HOST:-192.168.10.15}"
DEPLOY_PORT="${DEPLOY_PORT:-2210}"
DEPLOY_USER="${DEPLOY_USER:-root}"
DEPLOY_PATH="${DEPLOY_PATH:-/opt/aiyun-base}"

# ── 定位项目根目录（本脚本位于 <root>/scripts/ 下）──────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
EXCLUDE_FILE="${ROOT_DIR}/.deployignore"

#  颜色（非终端时自动关闭）────────────────────────────────────
if [ -t 1 ]; then
	RED=$'\033[31m'
	GREEN=$'\033[32m'
	YELLOW=$'\033[33m'
	BOLD=$'\033[1m'
	RESET=$'\033[0m'
else
	RED='' GREEN='' YELLOW='' BOLD='' RESET=''
fi

info() { printf '%s\n' "$*"; }
ok() { printf '%s✅ %s%s\n' "${GREEN}" "$*" "${RESET}"; }
warn() { printf '%s⚠️  %s%s\n' "${YELLOW}" "$*" "${RESET}"; }
die() {
	printf '%s❌ %s%s\n' "${RED}" "$*" "${RESET}" >&2
	exit 1
}

# ─ 参数解析 ──────────────────────────────────────────────────
DRY_RUN=false
DO_DELETE=false
USE_TAR=false

while [ $# -gt 0 ]; do
	case "$1" in
	-n | --dry-run)
		DRY_RUN=true
		shift
		;;
	--delete)
		DO_DELETE=true
		shift
		;;
	--tar)
		USE_TAR=true
		shift
		;;
	-h | --help)
		sed -n '2,18p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
		exit 0
		;;
	*)
		die "未知参数：$1（用 --help 查看用法）"
		;;
	esac
done

# ── 前置检查 ───────────────────────────────────────────────────
if [ "${USE_TAR}" = true ]; then
	command -v tar >/dev/null 2>&1 || die "本机没有 tar。"
else
	command -v rsync >/dev/null 2>&1 ||
		die "本机没有 rsync。装一个（Debian/Ubuntu: apt install rsync），或改用 --tar。"
fi

if [ ! -f "${EXCLUDE_FILE}" ]; then
	die "找不到排除清单：${EXCLUDE_FILE}"
fi

# 这三样东西不在 git 里（被 .gitignore 排除），但服务器必须有。
# 漏传的后果很隐蔽：nginx 起不来，或者前端能打开但 WebSocket 连不上。
if [ ! -f "${ROOT_DIR}/docker/nginx/ssl/server.pem" ]; then
	die "缺少证书 docker/nginx/ssl/server.pem。生成方法见 DEPLOYMENT.md 第 5.4 节。"
fi
if [ ! -f "${ROOT_DIR}/docker/nginx/ssl/server.key" ]; then
	die "缺少私钥 docker/nginx/ssl/server.key。生成方法见 DEPLOYMENT.md 第 5.4 节。"
fi
if [ ! -f "${ROOT_DIR}/docker/nginx/web/dist/index.html" ]; then
	die "缺少前端产物 docker/nginx/web/dist/。先在 frontend/web 构建，再复制过来（见 DEPLOYMENT.md 第 3.2 节）。"
fi

# 用 bash 内建的 /dev/tcp 探端口，不依赖 nc，也不会触发密码提示
if ! (exec 3<>"/dev/tcp/${DEPLOY_HOST}/${DEPLOY_PORT}") 2>/dev/null; then
	die "连不上 ${DEPLOY_HOST}:${DEPLOY_PORT}。检查服务器是否开机、SSH 端口是否为 ${DEPLOY_PORT}。"
fi
ok "服务器可达：${DEPLOY_HOST}:${DEPLOY_PORT}"

SSH_TARGET="${DEPLOY_USER}@${DEPLOY_HOST}"

# ── 上传方式：rsync ────────────────────────────────────────────
# .deployignore 中被排除的路径同时受 --delete 保护，
# 所以服务器上的 docker/.env、数据库、用户上传文件不会被误删。
do_rsync() {
	RSYNC_ARGS=(
		-az
		--info=progress2
		--stats
		--human-readable
		--exclude-from="${EXCLUDE_FILE}"
		-e "ssh -p ${DEPLOY_PORT}"
	)
	if [ "${DO_DELETE}" = true ]; then
		RSYNC_ARGS+=(--delete)
	fi
	if [ "${DRY_RUN}" = true ]; then
		RSYNC_ARGS+=(--dry-run -v)
		warn "预览模式：服务器上不会发生任何改动"
	fi

	set +e
	rsync "${RSYNC_ARGS[@]}" "${ROOT_DIR}/" "${SSH_TARGET}:${DEPLOY_PATH}/"
	RC=$?
	set -e

	if [ "${RC}" -eq 12 ]; then
		info ""
		die "rsync 协议错误（退出码 12）。最常见的原因是【服务器上没装 rsync】。
   两条路选一条：
     1. 在服务器上装：yum install -y rsync    （Debian/Ubuntu: apt install -y rsync）
     2. 改用 tar 传输：./scripts/upload.sh --tar"
	elif [ "${RC}" -eq 255 ]; then
		die "SSH 连接/认证失败（退出码 255）。检查 ${DEPLOY_USER} 用户、端口 ${DEPLOY_PORT} 和密码是否正确。"
	elif [ "${RC}" -eq 23 ] || [ "${RC}" -eq 24 ]; then
		warn "rsync 退出码 ${RC}：有文件在传输过程中被改动，已跳过。重跑一次即可补齐。"
	elif [ "${RC}" -ne 0 ]; then
		die "rsync 退出码 ${RC}。完整输出见上方。"
	fi
}

# ── 上传方式：tar over ssh ─────────────────────────────────────
# 用于服务器没装 rsync 的情况。全量传输，不支持 --delete。
# 注意 .deployignore 里不能写结尾斜杠，否则 GNU tar 会完全忽略该规则。
do_tar() {
	if [ "${DRY_RUN}" = true ]; then
		local tmp
		tmp="$(mktemp)"
		trap 'rm -f "${tmp}"' EXIT
		info "在本地展开排除清单（不连接服务器）…"
		tar --exclude-from="${EXCLUDE_FILE}" -czf - -C "${ROOT_DIR}" ./ 2>/dev/null |
			tar -tzf - | sed 's|^\./||' >"${tmp}"
		info ""
		info "将打包 $(wc -l <"${tmp}") 个条目。前 20 条："
		head -20 "${tmp}"
		info ""
		ok "预览完成。把 --tar 去掉 --dry-run 即开始传输。"
		return 0
	fi

	# 远端目录可能不存在，先建；已存在的文件会被覆盖
	if ! tar --exclude-from="${EXCLUDE_FILE}" -czf - -C "${ROOT_DIR}" ./ |
		ssh -p "${DEPLOY_PORT}" "${SSH_TARGET}" \
			"mkdir -p '${DEPLOY_PATH}' && tar -xzf - -C '${DEPLOY_PATH}'"; then
		die "tar 传输失败。检查服务器磁盘空间与 ${DEPLOY_PATH} 的写权限。"
	fi
}

# ── 执行 ──────────────────────────────────────────────────────
if [ "${USE_TAR}" = true ]; then
	MODE="流式传输（tar over ssh）"
elif [ "${DO_DELETE}" = true ]; then
	MODE="增量同步 + 删除多余文件（rsync）"
else
	MODE="增量同步（rsync）"
fi
if [ "${DRY_RUN}" = true ]; then
	MODE="预览（不传输）"
fi

info ""
info "${BOLD}源目录  ${RESET}${ROOT_DIR}/"
info "${BOLD}目标    ${RESET}${SSH_TARGET}:${DEPLOY_PATH}/"
info "${BOLD}排除清单${RESET}${EXCLUDE_FILE}"
info "${BOLD}模式    ${RESET}${MODE}"
info ""

if [ "${USE_TAR}" = true ]; then
	warn "--tar 模式：全量传输、不支持 --delete，重复部署会比 rsync 慢"
elif [ "${DO_DELETE}" = true ] && [ "${DRY_RUN}" = false ]; then
	warn "--delete 已启用：服务器上不在本次传输范围内的文件会被删除（.deployignore 中的路径除外）"
fi

if [ "${USE_TAR}" = true ]; then
	do_tar
else
	do_rsync
fi

info ""

# ── 结果 ─────────────────────────────────────────────────────
if [ "${DRY_RUN}" = true ]; then
	ok "预览完成。去掉 --dry-run 即开始实际传输。"
	exit 0
fi

ok "上传完成"
info ""
info "下一步在服务器上执行："
info "  cd ${DEPLOY_PATH}"
info "  cp docker/.env.example docker/.env                       # 首次部署才需要"
info "  cp backend/env/.env.prod.example backend/env/.env.prod   # 首次部署才需要，记得改 SECRET_KEY"
info "  ./deploy.sh"