#!/bin/bash
set -euo pipefail

# ==================== 配置 ====================
# 部署方式：本地开发完成后将项目目录上传至服务器（scp/FTP/面板均可），
# 再在服务器上执行本脚本完成构建与启动——脚本不再从 git 拉取代码。
PROJECT_NAME="FastapiAdmin"
WORK_DIR="$(cd "$(dirname "$0")" && pwd)"
DOCKER_DIR="${WORK_DIR}/docker"
ENV_FILE="${DOCKER_DIR}/.env"

COLOR_GREEN='\033[0;32m'; COLOR_BLUE='\033[0;34m'; COLOR_YELLOW='\033[0;33m'; COLOR_RED='\033[0;31m'; COLOR_RESET='\033[0m'

log() {
    local color; local level=${2:-INFO}
    case $level in
        INFO) color="${COLOR_BLUE}" ;; WARN) color="${COLOR_YELLOW}" ;;
        ERROR) color="${COLOR_RED}" ;; SUCCESS) color="${COLOR_GREEN}" ;;
        *) color="${COLOR_RESET}" ;;
    esac
    echo -e "${color}[$(date '+%Y-%m-%d %H:%M:%S')] [${level}] ${1}${COLOR_RESET}"
}

# ==================== 核心函数 ====================

load_env() {
    if [ -f "${ENV_FILE}" ]; then
        set -a; source "${ENV_FILE}"; set +a
    elif [ -f "${DOCKER_DIR}/.env.example" ]; then
        cp "${DOCKER_DIR}/.env.example" "${ENV_FILE}"
        set -a; source "${ENV_FILE}"; set +a
        log "已从 .env.example 生成 ${ENV_FILE}，请先填写密码等配置后重新执行" "WARN"
        exit 1
    else
        log ".env 文件不存在" "ERROR"; exit 1
    fi
    log "✅ 环境变量已加载" "SUCCESS"
}

check_deps() {
    for dir in "${DOCKER_DIR}/mysql/data" "${DOCKER_DIR}/redis/data" "${DOCKER_DIR}/mysql/init" "${DOCKER_DIR}/redis/conf" "${WORK_DIR}/backend/static/upload"; do
        [ -d "$dir" ] || mkdir -p "$dir"
    done
    local missing=()
    for cmd in docker; do
        command -v $cmd &>/dev/null || missing+=($cmd)
    done
    if [ ${#missing[@]} -gt 0 ]; then
        log "缺少依赖: ${missing[*]}" "ERROR"; exit 1
    fi

    # 必须是 Compose v2（docker compose 子命令）。
    # 【不要】退回 docker-compose（v1）：本项目的 compose 文件用了
    #   - depends_on.condition: service_healthy  等待 MySQL 就绪再启动 backend
    #   - deploy.resources.limits.cpus           容器 CPU 限制
    # v1 对前者不支持、对后者静默忽略，结果是「启动顺序错乱 + 资源限制失效」，
    # 而且要到运行期才暴露，比直接报错难查得多。
    if ! docker compose version &>/dev/null; then
        log "未找到 Docker Compose v2（docker compose 命令）" "ERROR"
        if command -v docker-compose &>/dev/null; then
            log "检测到 docker-compose（v1）: $(docker-compose --version 2>/dev/null)" "WARN"
            log "v1 无法正确执行本项目的 compose 文件，请勿改用它。" "WARN"
        fi
        log "安装方式见 DEPLOYMENT.md 第 2 节；若已配置 docker-ce 软件源：" "INFO"
        log "  yum install -y docker-compose-plugin" "INFO"
        exit 1
    fi

    log "✅ 依赖检查通过（Docker Compose $(docker compose version --short 2>/dev/null)）" "SUCCESS"
}

check_code() {
    # 代码由用户上传，这里仅校验关键文件齐全
    local missing=()
    [ -f "${WORK_DIR}/backend/main.py" ] || missing+=("backend/main.py")
    [ -f "${DOCKER_DIR}/backend/Dockerfile" ] || missing+=("docker/backend/Dockerfile")
    [ -f "${DOCKER_DIR}/redis/conf/redis.conf" ] || missing+=("docker/redis/conf/redis.conf")
    if [ ${#missing[@]} -gt 0 ]; then
        log "代码不完整，缺少: ${missing[*]}（请重新上传后重试）" "ERROR"; exit 1
    fi
    log "✅ 代码完整性检查通过" "SUCCESS"
}

build_image() {
    cd "${DOCKER_DIR}"
    export DOCKER_BUILDKIT=1
    docker compose build || { log "镜像构建失败" "ERROR"; exit 1; }
    log "✅ 镜像构建完成" "SUCCESS"
}

start_service() {
    cd "${DOCKER_DIR}"
    docker compose up -d --force-recreate || { log "容器启动失败" "ERROR"; exit 1; }
    log "⏳ 等待服务就绪..."
    for i in $(seq 1 30); do
        if docker compose ps mysql --format '{{.Status}}' 2>/dev/null | grep -q "healthy"; then
            log "✅ MySQL 已就绪" "SUCCESS"; break
        fi
        sleep 2
    done
    docker compose ps
    log "✅ 服务启动完成" "SUCCESS"
}

stop_service() {
    cd "${DOCKER_DIR}" 2>/dev/null || true
    docker compose down 2>/dev/null || true
    log "✅ 服务已停止" "SUCCESS"
}

show_logs() {
    cd "${DOCKER_DIR}"
    docker compose ps --format "table {{.Service}}\t{{.Name}}\t{{.Status}}\t{{.Ports}}"
    echo "--- 最近 50 行日志 ---"
    docker compose logs --tail=50 2>/dev/null
}

verify() {
    # 必须 cd 到 docker 目录：compose 文件在那里。
    # 漏了这一步时，`./deploy.sh verify` 单独调用会因为找不到 compose 文件
    # 而把每个服务都报成 "not found"（完整部署时因为前面的函数已经 cd 过，
    # 反而看不出问题——所以这个 bug 只在单独调用时暴露）。
    cd "${DOCKER_DIR}"
    local ok=true
    for svc in mysql redis backend nginx; do
        local st=$(docker compose ps "$svc" --format '{{.Status}}' 2>/dev/null)
        if [ -z "$st" ]; then
            log "❌ $svc: 未找到容器" "ERROR"; ok=false
        elif echo "$st" | grep -qE "Up|healthy"; then
            log "✅ $svc: $st" "SUCCESS"
        else
            log "❌ $svc: $st" "ERROR"; ok=false
        fi
    done
    $ok && log "✅ 所有服务正常" "SUCCESS" || log "⚠️ 部分服务异常" "WARN"
}

cleanup() {
    # 仅清理悬空镜像/网络/构建缓存；-a 会删掉服务器上其他项目的未使用镜像，慎用
    docker image prune -f >/dev/null 2>&1 || true
    docker builder prune -f >/dev/null 2>&1 || true
    log "✅ 构建缓存已清理" "SUCCESS"
}

# ==================== 主流程 ====================

full_deploy() {
    log "========== 🚀 开始完整部署 ==========" "INFO"
    log "时间: $(date '+%Y-%m-%d %H:%M:%S')"
    load_env
    check_deps
    check_code
    build_image
    start_service
    verify
    show_logs
    cleanup
    log "========== 🎉 部署完成 ==========" "SUCCESS"
}

# ==================== 入口 ====================

trap 'stop_service; exit 130' INT TERM

case ${1:-} in
    start)   load_env; start_service ;;
    stop)    load_env; stop_service ;;
    restart) load_env; cd "${DOCKER_DIR}"; docker compose restart; docker compose ps ;;
    logs)    load_env; show_logs ;;
    verify)  load_env; verify ;;
    clean)   cleanup ;;
    help|-h|--help)
        echo "用法: $0 [命令]"
        echo "  无参数    完整部署（校验代码→构建→启动→清理；代码需先上传至服务器）"
        echo "  start     启动服务"
        echo "  stop      停止服务"
        echo "  restart   重启服务"
        echo "  logs      查看日志"
        echo "  verify    验证服务"
        echo "  clean     清理构建缓存"
        ;;
    *) full_deploy "$@" ;;
esac
