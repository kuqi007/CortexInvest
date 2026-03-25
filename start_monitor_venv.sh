#!/bin/bash
# 使用虚拟环境启动监控脚本
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$DIR/.venv"

# 检查虚拟环境是否存在
if [ ! -d "$VENV_DIR" ]; then
    echo "错误: 虚拟环境不存在，请先运行 setup 安装依赖"
    exit 1
fi

# 激活虚拟环境
source "$VENV_DIR/bin/activate"

# 运行原启动脚本
exec "$DIR/start_monitor.sh" "$@"
