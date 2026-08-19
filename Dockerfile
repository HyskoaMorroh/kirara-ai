# 第一阶段：WebUI 产物与目标 CPU 架构无关，始终在原生构建平台生成
FROM --platform=$BUILDPLATFORM node:20-bookworm-slim AS frontend-builder

ENV NODE_OPTIONS=--max-old-space-size=3072

WORKDIR /webui
COPY webui/package.json webui/yarn.lock ./
RUN corepack enable && \
    for attempt in 1 2 3; do \
        yarn install --frozen-lockfile --network-timeout 120000 && break; \
        if [ "$attempt" = 3 ]; then exit 1; fi; \
        echo "Yarn registry download failed; retrying install ($attempt/3)..."; \
        sleep $((attempt * 10)); \
    done
COPY webui/ ./
ARG VITE_APP_VERSION
ENV VITE_APP_VERSION=${VITE_APP_VERSION}
RUN yarn build

# 第二阶段：构建wheel包
FROM python:3.11-slim AS builder

WORKDIR /build
COPY pyproject.toml README.md LICENSE MANIFEST.in uv.lock ./
COPY kirara_ai ./kirara_ai
RUN python -m pip install --no-cache-dir uv build && \
    uv export --frozen --no-dev --no-emit-project --format requirements-txt --output-file requirements.txt && \
    python -m build

# 第三阶段：运行环境
FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive

# 复制字体文件
COPY ./data/fonts/sarasa-mono-sc-regular.ttf /usr/share/fonts/

# 安装系统依赖
RUN if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources; \
    fi && \
    if [ -f /etc/apt/sources.list ]; then \
        sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list; \
    fi && \
    apt-get -o Acquire::Retries=5 -yqq update && \
    apt-get -o Acquire::Retries=5 -yqq install --no-install-recommends \
        ffmpeg \
        libmagic1 && \
    apt-get -yq clean && \
    apt-get -yq purge --auto-remove -o APT::AutoRemove::RecommendsImportant=false && \
    rm -rf /var/lib/apt/lists/*

# 创建应用目录
WORKDIR /app

# 复制第一阶段构建的wheel包并安装
COPY --from=builder /build/dist/*.whl /app/
COPY --from=builder /build/requirements.txt /app/

# 安装后端并复制由固定前端源码构建的 WebUI
RUN pip install --no-cache-dir --require-hashes -r requirements.txt && \
    pip install --no-cache-dir --no-deps *.whl && \
    pip cache purge && \
    rm *.whl requirements.txt
COPY --from=frontend-builder /webui/dist /app/web

# 复制应用代码
COPY ./docker/start.sh /app/docker/
COPY ./data /tmp/data
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/', timeout=3).read(1)" || exit 1

CMD ["/bin/bash", "/app/docker/start.sh"]
