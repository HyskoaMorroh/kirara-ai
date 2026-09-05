# 第一阶段：所有派生版本必须与 pyproject.toml 的唯一版本源一致
FROM python:3.11-slim AS version-check

WORKDIR /source
COPY . /source
# Keep the version tool explicit so the release gate remains visible in the Dockerfile.
COPY scripts/version.py ./scripts/version.py
RUN python scripts/version.py check && \
    python scripts/version.py tag > /release-tag && \
    python scripts/version.py npm > /npm-version

# 第二阶段：WebUI 产物与目标 CPU 架构无关，始终在原生构建平台生成
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
COPY --from=version-check /release-tag /release-tag
COPY --from=version-check /npm-version /npm-version
ARG VITE_APP_VERSION
ENV VITE_APP_VERSION=${VITE_APP_VERSION}
RUN expected_version="$(cat /release-tag)" && \
    if [ -n "${VITE_APP_VERSION}" ] && [ "${VITE_APP_VERSION}" != "${expected_version}" ]; then \
        echo "VITE_APP_VERSION ${VITE_APP_VERSION} does not match ${expected_version}" >&2; \
        exit 1; \
    fi && \
    export VITE_APP_VERSION="${expected_version}" && \
    yarn build && \
    node -e "const fs=require('fs');const m=JSON.parse(fs.readFileSync('dist/version.json','utf8'));const p=fs.readFileSync('/npm-version','utf8').trim();if(m.version!==process.env.VITE_APP_VERSION||m.packageVersion!==p)throw new Error(JSON.stringify(m))"

# 第三阶段：构建wheel包
FROM python:3.11-slim AS builder

WORKDIR /build
COPY pyproject.toml README.md LICENSE MANIFEST.in uv.lock ./
COPY kirara_ai ./kirara_ai
RUN python -m pip install --no-cache-dir uv build && \
    uv export --frozen --no-dev --no-emit-project --format requirements-txt --output-file requirements.txt && \
    python -m build

# 第四阶段：运行环境
FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive

# 复制字体文件
COPY ./data/fonts/sarasa-mono-sc-regular.ttf /usr/share/fonts/

# 安装系统依赖
#
# `poppler-utils` 是随包 pdf 技能里 `pdf2image` 的**运行前提**：那个包只是
# poppler 的 Python 绑定，缺了二进制会在调用时抛 `PDFInfoNotInstalledError`，
# 而报错文字与「PDF 转图片」这件事看不出关系。约 15MB。
#
# `fonts-noto-cjk` 是文档生成技能的前提：容器里没有中文字体时，生成的
# PPT/PDF 里所有中文渲染成方框。文件本身是好的、打开才发现看不了，
# 而那时用户会以为是文件损坏。约 50MB。
RUN if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources; \
    fi && \
    if [ -f /etc/apt/sources.list ]; then \
        sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list; \
    fi && \
    apt-get -o Acquire::Retries=5 -yqq update && \
    apt-get -o Acquire::Retries=5 -yqq install --no-install-recommends \
        ffmpeg \
        libmagic1 \
        poppler-utils \
        fonts-noto-cjk && \
    apt-get -yq clean && \
    apt-get -yq purge --auto-remove -o APT::AutoRemove::RecommendsImportant=false && \
    rm -rf /var/lib/apt/lists/*

# 创建应用目录
WORKDIR /app

# 复制第一阶段构建的wheel包并安装
COPY --from=builder /build/dist/*.whl /app/
COPY --from=builder /build/requirements.txt /app/

# 安装后端并复制由固定前端源码构建的 WebUI
RUN --mount=type=cache,target=/root/.cache/pip \
    for attempt in 1 2 3; do \
        pip install --require-hashes --timeout 120 --retries 10 -r requirements.txt && break; \
        if [ "$attempt" = 3 ]; then exit 1; fi; \
        echo "PyPI dependency download failed; retrying install ($attempt/3)..."; \
        sleep $((attempt * 10)); \
    done && \
    pip install --no-cache-dir --no-deps *.whl && \
    rm *.whl requirements.txt
COPY --from=frontend-builder /webui/dist /app/web

# 复制应用代码
COPY ./docker/start.sh /app/docker/
# 只带入首次启动所需的受版本控制默认值。运行期数据库、资源注册表、
# 会话、插件和审计记录必须由挂载的 /app/data 提供，不能进入镜像。
COPY ./data/dispatch_rules /tmp/data/dispatch_rules
COPY ./data/workflows /tmp/data/workflows
COPY ./data/fonts /tmp/data/fonts
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/', timeout=3).read(1)" || exit 1

CMD ["/bin/bash", "/app/docker/start.sh"]
