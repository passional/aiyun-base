# ============================================================================
# FastapiAdmin 后端镜像（源码构建路径）
#
# 构建参数 DB 决定安装哪套数据库驱动：
#   docker build --build-arg DB=mysql    -f docker/backend/Dockerfile .   # 默认
#   docker build --build-arg DB=postgres -f docker/backend/Dockerfile .
#
# 注意：正式部署建议用 deploy-artifacts.sh（本地构建镜像 → docker save/load），
#      这样服务器上不需要源码，也不需要构建工具链。
# ============================================================================
FROM python:3.12-slim

LABEL maintainer="948080782@qq.com"

ARG DB=mysql

ENV TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LANG=C.UTF-8 \
    DB=${DB}

WORKDIR /home

# 依赖清单先复制（layer 缓存：只改代码时不重装依赖）
COPY ./backend/requirements/ /home/requirements/

# 只装所选数据库的驱动 + 存储与加密依赖
# pip 源通过 build arg 注入，方便在不同网络环境切换，默认阿里云。
# 换源：在 docker/.env 里设置 PIP_INDEX_URL，或临时执行
#   PIP_INDEX_URL=https://xxx/simple/ docker compose build
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
RUN pip install --no-cache-dir \
      -r /home/requirements/base.txt \
      -r /home/requirements/db/${DB}.txt \
      -r /home/requirements/storage.txt \
      -i "${PIP_INDEX_URL}"

# 应用代码（backend/.venv、logs、缓存、真实 .env.* 由根 .dockerignore 排除）
COPY ./backend/ /home/

# 以非 root 用户运行；运行时需要写的目录预先建好并交给该用户
RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin app \
 && mkdir -p /home/logs /home/static/upload /home/alembic/versions \
 && chown -R app:app /home
USER app

EXPOSE 8001

CMD ["python", "main.py", "run", "--env=prod"]
