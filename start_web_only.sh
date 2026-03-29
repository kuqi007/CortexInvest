#!/bin/bash
# 只启动 Web 端（只读 Dashboard）
# 用于不需要运行 Python 数据采集的电脑

DIR="$(cd "$(dirname "$0")" && pwd)"
WEB_LOG="$DIR/logs/web.log"
WEB_PID_FILE="$DIR/.web.pid"

_is_running() {
    [ -f "$WEB_PID_FILE" ] && kill -0 "$(cat "$WEB_PID_FILE")" 2>/dev/null
}

_stop() {
    if [ -f "$WEB_PID_FILE" ]; then
        PID=$(cat "$WEB_PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            kill "$PID" 2>/dev/null && echo "Web 已停止 (pid=$PID)"
            rm -f "$WEB_PID_FILE"
        else
            rm -f "$WEB_PID_FILE"
        fi
    fi
    # 兜底清理
    pkill -f "next dev" 2>/dev/null || true
}

case "${1:-}" in
    stop)
        _stop
        ;;
    restart)
        _stop
        sleep 1
        $0 start
        ;;
    status)
        if _is_running; then
            echo "Web 运行中  pid=$(cat "$WEB_PID_FILE")"
        else
            echo "Web 未运行"
        fi
        ;;
    start|*)
        # 检查数据目录
        if [ ! -L "$DIR/src/data" ] && [ ! -d "$DIR/src/data" ]; then
            echo "错误: src/data 目录不存在"
            echo "请先运行 setup_on_new_machine.sh 设置数据目录"
            exit 1
        fi

        if _is_running; then
            echo "Web 已在运行  pid=$(cat "$WEB_PID_FILE")"
            exit 0
        fi

        mkdir -p "$DIR/logs"

        echo "启动 Web Dashboard (只读模式)..."
        cd "$DIR/web"
        nohup npm run dev > "$WEB_LOG" 2>&1 &
        WEB_PID=$!
        echo $WEB_PID > "$WEB_PID_FILE"
        echo "Web 启动  pid=$WEB_PID  日志=$WEB_LOG"
        echo ""
        echo "访问: http://localhost:3120"
        ;;
esac
