# 第一阶段：构建固定版本的 WebUI
FROM node:20-bookworm-slim AS frontend-builder

WORKDIR /webui
COPY webui/package.json webui/yarn.lock ./
RUN corepack enable && yarn install --frozen-lockfile
COPY webui/ ./
RUN yarn build

# 第二阶段：构建wheel包
FROM python:3.11-slim AS builder

WORKDIR /build
COPY . .
RUN python -m pip install build && \
    python -m build

# 第三阶段：运行环境
FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive

# 复制字体文件
COPY ./data/fonts/sarasa-mono-sc-regular.ttf /usr/share/fonts/

# 安装系统依赖
RUN apt-get -yqq update && \
    apt-get -yqq install --no-install-recommends \
        ffmpeg \
        libmagic1 && \
    apt-get -yq clean && \
    apt-get -yq purge --auto-remove -o APT::AutoRemove::RecommendsImportant=false && \
    rm -rf /var/lib/apt/lists/*

# 创建应用目录
WORKDIR /app

# 复制第一阶段构建的wheel包并安装
COPY --from=builder /build/dist/*.whl /app/

# 安装后端并复制由固定前端源码构建的 WebUI
RUN pip install --no-cache-dir *.whl && \
    pip cache purge && \
    rm *.whl
COPY --from=frontend-builder /webui/dist /app/web

# 复制应用代码
COPY ./docker/start.sh /app/docker/
COPY ./data /tmp/data
EXPOSE 8080

CMD ["/bin/bash", "/app/docker/start.sh"]
