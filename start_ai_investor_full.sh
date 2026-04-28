#!/usr/bin/env bash
# 盯盘系统启停脚本 — 全后台运行，关掉终端也不影响
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
POLLER_PID="$DIR/.poller.pid"
NOTIFIER_PID="$DIR/.notifier.pid"
L2_DAEMON_PID="$DIR/.l2_daemon.pid"
L2_DAEMON_WATCHDOG_PID="$DIR/.l2_daemon_watchdog.pid"
TICK_MONITOR_PID="$DIR/.tick_monitor.pid"
WEB_PID="$DIR/.web.pid"
POLLER_LOG="$DIR/logs/poller.log"
NOTIFIER_LOG="$DIR/logs/notifier.log"
L2_DAEMON_LOG="$DIR/logs/l2_daemon_out.log"
TICK_MONITOR_LOG="$DIR/logs/tick_monitor.log"
WEB_LOG="$DIR/logs/web.log"

mkdir -p "$DIR/logs"

# 按日期生成日志文件名
TODAY=$(date +%Y-%m-%d)
POLLER_LOG="$DIR/logs/poller-$TODAY.log"
NOTIFIER_LOG="$DIR/logs/notifier-$TODAY.log"
L2_DAEMON_LOG="$DIR/logs/l2_daemon-$TODAY.log"
TICK_MONITOR_LOG="$DIR/logs/tick_monitor-$TODAY.log"
WEB_LOG="$DIR/logs/web-$TODAY.log"

_check_terminal_notifier() {
  if ! command -v terminal-notifier &> /dev/null; then
    echo "ERROR: terminal-notifier 未安装"
    echo ""
    echo "L1/L2 告警弹窗功能需要 terminal-notifier，请安装:"
    echo ""
    echo "  brew install terminal-notifier"
    echo ""
    exit 1
  fi
}

_read_pid() { [ -f "$1" ] && cat "$1" || echo ""; }

_is_running() {
  local pid=$(_read_pid "$1")
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

# Find actual Python child PID under nohup/uv wrapper
_get_python_pid() {
  local wrapper_pid=$1
  [ -z "$wrapper_pid" ] && return
  # Try pgrep first (most reliable on macOS/Linux)
  local child=$(pgrep -P "$wrapper_pid" 2>/dev/null | head -1)
  if [ -n "$child" ]; then
    echo "$child"
    return
  fi
  # Fallback: ps approach
  child=$(ps -o pid= --ppid "$wrapper_pid" 2>/dev/null | tr -d ' ' | head -1)
  [ -n "$child" ] && echo "$child"
}

# Check if a Python script process is actually running (checks child process)
_is_python_running() {
  local pidfile=$1 pattern=$2
  local wrapper_pid=$(_read_pid "$pidfile")
  [ -z "$wrapper_pid" ] && return 1
  
  # Check if nohup/uv wrapper is alive
  if ! kill -0 "$wrapper_pid" 2>/dev/null; then
    return 1
  fi
  
  # Check if actual Python child process is running
  local python_pid=$(_get_python_pid "$wrapper_pid")
  if [ -n "$python_pid" ]; then
    kill -0 "$python_pid" 2>/dev/null && return 0
    return 1
  fi
  
  # Fallback: check by pattern if child pid not found
  pgrep -f "$pattern" > /dev/null 2>&1
}

_stop_one() {
  local pidfile=$1 label=$2 pattern=$3
  local pid=$(_read_pid "$pidfile")
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    # 先杀 Python 子进程（uv run → python），再杀 wrapper
    local child_pid=$(pgrep -P "$pid" 2>/dev/null | head -1)
    if [ -n "$child_pid" ]; then
      kill "$child_pid" 2>/dev/null || true
    fi
    kill -- -"$pid" 2>/dev/null || kill "$pid" 2>/dev/null
    echo "$label 已停止 (pid=$pid)"
  fi
  rm -f "$pidfile"
  # 兜底：用命令模式清理可能的孤儿进程
  if [ -n "$pattern" ]; then
    pkill -f "$pattern" 2>/dev/null || true
  fi
}

_ensure_no_orphan() {
  # 启动前兜底：若 PID 文件不存在/失效但进程实际还在，先杀掉
  local pidfile=$1 pattern=$2
  if ! _is_running "$pidfile" && pgrep -f "$pattern" > /dev/null 2>&1; then
    echo "  检测到孤儿进程 ($pattern)，清理中..."
    pkill -f "$pattern" 2>/dev/null || true
    sleep 0.5
  fi
}

do_start() {
  # 清理 OneDrive 同步冲突残留文件（带机器ID后缀的副本，如 -ADSKKN7X1GJJYG）
  find "$DIR/src/data" -name '*-[A-Z0-9][A-Z0-9][A-Z0-9][A-Z0-9][A-Z0-9][A-Z0-9][A-Z0-9][A-Z0-9][A-Z0-9][A-Z0-9][A-Z0-9]*' -delete 2>/dev/null || true

  # 检查 terminal-notifier 依赖
  _check_terminal_notifier

  # DB split migration (skip Python if already done)
  DATA_DIR="$DIR/src/data"
  if [ -f "$DATA_DIR/config.db" ] && [ -f "$DATA_DIR/trading.db" ]; then
    : # already migrated
  else
    echo "Checking DB migration..."
    uv run python scripts/migrate_split_db.py
  fi

  # 早间简报（Python 内部检查日期）
  cd "$DIR"
  uv run python -c "from src.tools.daily_summary_generator import generate_morning_briefing; generate_morning_briefing()" >> "$DIR/logs/morning_briefing.log" 2>&1

  # Poller
  _ensure_no_orphan "$POLLER_PID" "market_data_poller.py"
  if _is_running "$POLLER_PID"; then
    echo "Poller 已在运行 (pid=$(_read_pid "$POLLER_PID"))，跳过"
  else
    cd "$DIR"
    nohup uv run python src/tools/market_data_poller.py >> "$POLLER_LOG" 2>&1 &
    echo $! > "$POLLER_PID"
    echo "Poller  启动  pid=$!  日志=logs/poller-$TODAY.log"
  fi

  # Notifier (给 poller 一个短暂启动窗口)
  _ensure_no_orphan "$NOTIFIER_PID" "stock_notifier.py"
  if _is_running "$NOTIFIER_PID"; then
    echo "Notifier 已在运行 (pid=$(_read_pid "$NOTIFIER_PID"))，跳过"
  else
    cd "$DIR"
    sleep 2
    nohup uv run python src/tools/stock_notifier.py >> "$NOTIFIER_LOG" 2>&1 &
    echo $! > "$NOTIFIER_PID"
    echo "Notifier 启动  pid=$!  日志=logs/notifier-$TODAY.log"
  fi

  # L2 Strategy Daemon (optional, needs Futu OpenD)
  _ensure_no_orphan "$L2_DAEMON_PID" "l2_strategy_daemon.py"
  if _is_running "$L2_DAEMON_PID"; then
    echo "L2 Daemon 已在运行 (pid=$(_read_pid "$L2_DAEMON_PID"))，跳过"
  else
    cd "$DIR"
    nohup uv run python src/tools/l2_strategy_daemon.py >> "$L2_DAEMON_LOG" 2>&1 &
    local l2_wrapper_pid=$!
    echo $l2_wrapper_pid > "$L2_DAEMON_PID"
    echo "L2 Daemon 启动  pid=$l2_wrapper_pid  日志=logs/l2_daemon-$TODAY.log"

    # 启动 watchdog：每 60s 检查 daemon 是否存活，异常退出则自动重启
    (
      local watched_pid=$l2_wrapper_pid
      local restart_count=0
      local first_fail_ts=0
      while true; do
        sleep 60
        # PID 文件被删 = 正常停止，watchdog 退出
        if [ ! -f "$L2_DAEMON_PID" ]; then
          exit 0
        fi
        # 检查 wrapper + python 子进程是否存活
        local python_alive=false
        if kill -0 "$watched_pid" 2>/dev/null; then
          local child=$(_get_python_pid "$watched_pid")
          if [ -n "$child" ] && kill -0 "$child" 2>/dev/null; then
            python_alive=true
          fi
        fi
        if [ "$python_alive" = false ]; then
          # 清理可能的孤儿进程
          local orphan=$(pgrep -f "l2_strategy_daemon.py" | head -1)
          if [ -n "$orphan" ]; then
            kill "$orphan" 2>/dev/null || true
            sleep 1
          fi
          # 重启限制：5 分钟内最多 3 次
          local now_ts=$(date +%s)
          if [ $restart_count -eq 0 ] || [ $((now_ts - first_fail_ts)) -gt 300 ]; then
            restart_count=1
            first_fail_ts=$now_ts
          else
            restart_count=$((restart_count + 1))
          fi
          if [ $restart_count -gt 3 ]; then
            echo "[$(date '+%H:%M:%S')] watchdog: L2 Daemon 5分钟内崩溃${restart_count}次，放弃重启" >> "$L2_DAEMON_LOG"
            rm -f "$L2_DAEMON_PID"
            exit 1
          fi
          echo "[$(date '+%H:%M:%S')] watchdog: L2 Daemon 异常退出，第${restart_count}次重启..." >> "$L2_DAEMON_LOG"
          cd "$DIR"
          nohup uv run python src/tools/l2_strategy_daemon.py >> "$L2_DAEMON_LOG" 2>&1 &
          watched_pid=$!
          echo $watched_pid > "$L2_DAEMON_PID"
        fi
      done
    ) &
    echo $! > "$L2_DAEMON_WATCHDOG_PID"
  fi

  # Tick Monitor (短线盯盘，可选)
  _ensure_no_orphan "$TICK_MONITOR_PID" "tick_monitor.py"
  if _is_running "$TICK_MONITOR_PID"; then
    echo "Tick Monitor 已在运行 (pid=$(_read_pid "$TICK_MONITOR_PID"))，跳过"
  else
    cd "$DIR"
    nohup uv run python src/tools/tick_monitor.py >> "$TICK_MONITOR_LOG" 2>&1 &
    echo $! > "$TICK_MONITOR_PID"
    echo "Tick Monitor 启动  pid=$!  日志=logs/tick_monitor-$TODAY.log"
  fi

  # Web
  _ensure_no_orphan "$WEB_PID" "next dev"
  if _is_running "$WEB_PID"; then
    echo "Web    已在运行 (pid=$(_read_pid "$WEB_PID"))，跳过"
  else
    cd "$DIR/web"
    nohup npm run dev >> "$WEB_LOG" 2>&1 &
    echo $! > "$WEB_PID"
    echo "Web    启动  pid=$!  日志=logs/web-$TODAY.log"
  fi

  echo ""
  echo "全部后台运行中，可关闭终端。"
  echo "  查看状态: ./start_ai_investor_full.sh status"
  echo "  查看日志: tail -f logs/poller-$TODAY.log logs/notifier-$TODAY.log logs/l2_daemon-$TODAY.log logs/tick_monitor-$TODAY.log logs/web-$TODAY.log"
  echo "  停止服务: ./start_ai_investor_full.sh stop"
}

do_stop() {
  # 先杀 watchdog，防止它在 daemon 被杀后自动重启
  _stop_one "$L2_DAEMON_WATCHDOG_PID" "L2 Daemon Watchdog" ""
  _stop_one "$NOTIFIER_PID" "Notifier" "stock_notifier.py"
  _stop_one "$L2_DAEMON_PID" "L2 Daemon" "l2_strategy_daemon.py"
  _stop_one "$TICK_MONITOR_PID" "Tick Monitor" "tick_monitor.py"
  _stop_one "$POLLER_PID" "Poller" "market_data_poller.py"
  _stop_one "$WEB_PID" "Web" "next-router-worker\|next dev"
}

do_status() {
  local wrapper_pid python_pid

  # Poller
  wrapper_pid=$(_read_pid "$POLLER_PID")
  if _is_python_running "$POLLER_PID" "market_data_poller.py"; then
    python_pid=$(_get_python_pid "$wrapper_pid")
    echo "Poller  运行中  pid=$python_pid (wrapper=$wrapper_pid)"
  else
    echo "Poller  未运行"
    rm -f "$POLLER_PID"
  fi

  # Notifier
  wrapper_pid=$(_read_pid "$NOTIFIER_PID")
  if _is_python_running "$NOTIFIER_PID" "stock_notifier.py"; then
    python_pid=$(_get_python_pid "$wrapper_pid")
    echo "Notifier 运行中  pid=$python_pid (wrapper=$wrapper_pid)"
  else
    echo "Notifier 未运行"
    rm -f "$NOTIFIER_PID"
  fi

  # L2 Daemon
  wrapper_pid=$(_read_pid "$L2_DAEMON_PID")
  if _is_python_running "$L2_DAEMON_PID" "l2_strategy_daemon.py"; then
    python_pid=$(_get_python_pid "$wrapper_pid")
    echo "L2 Daemon 运行中  pid=$python_pid (wrapper=$wrapper_pid)"
  else
    echo "L2 Daemon 未运行"
    rm -f "$L2_DAEMON_PID"
  fi

  # Tick Monitor
  wrapper_pid=$(_read_pid "$TICK_MONITOR_PID")
  if _is_python_running "$TICK_MONITOR_PID" "tick_monitor.py"; then
    python_pid=$(_get_python_pid "$wrapper_pid")
    echo "Tick Monitor 运行中  pid=$python_pid (wrapper=$wrapper_pid)"
  else
    echo "Tick Monitor 未运行"
    rm -f "$TICK_MONITOR_PID"
  fi

  # Web
  if _is_running "$WEB_PID"; then
    echo "Web      运行中  pid=$(_read_pid "$WEB_PID")"
  else
    echo "Web      未运行"
    rm -f "$WEB_PID"
  fi
}

do_restart() {
  do_stop
  sleep 1
  do_start
}

case "${1:-start}" in
  start)   do_start   ;;
  stop)    do_stop    ;;
  status)  do_status  ;;
  restart) do_restart ;;
  *)
    echo "用法: $0 {start|stop|status|restart}"
    exit 1
    ;;
esac
