#!/bin/bash
cd /app

# Copy default data
# check if data directory exists
if [ ! -d "/app/data" ]; then
    echo "Data directory does not exist, creating..."
    mkdir /app/data
fi

# check if data directory empty
if [ -z "$(ls -A /app/data)" ]; then
    echo "Data directory is empty, copying default data..."
    cp -r /tmp/data/. /app/data
fi

# create default config
if [ ! -f "/app/data/config.yaml" ]; then
    echo "Config file does not exist, creating..."
    # 必须配置 web，否则无法访问
    cat <<EOF > /app/data/config.yaml
web:
    host: 0.0.0.0
    port: 8080
EOF
fi

# create data/venv
if [ ! -d "/app/data/venv" ]; then
    echo "Venv directory does not exist, creating..."
    python -m venv /app/data/venv --system-site-packages
fi

# activate venv
source /app/data/venv/bin/activate

# `exec` 是必须的：不用它时容器里 PID 1 是这个 bash，而 bash 在等待子进程期间
# 不转发信号，于是 `docker stop` / `docker compose down` 发来的 SIGTERM 被它吞掉，
# 10 秒宽限期后整个容器被 SIGKILL。后果不是「关得不优雅」而是 `entry.py` 的
# 整段 finally 一次都不跑：记忆的异步写队列没 flush、出站队列里 `sending` 状态的
# 投递没被隔离成 `ambiguous`（下次启动恢复面对的是不完整现场）、适配器不走
# `stop()` 因此反向 WebSocket 被硬切。而「重启后无缝恢复」整个前提是
# 「上一次是干净停下的」。
#
# 换成 exec 之后 Python 直接成为 PID 1，`signal.signal(SIGTERM, ...)` 收到的就是
# Docker 发的那个信号。这里到这一行 bash 已无事可做，继续存在只是挡住信号。
exec python -m kirara_ai
