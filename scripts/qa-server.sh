#!/bin/bash
# Canonical dev-server lifecycle for the QA agent.
# The QA agent uses this script as its ONLY interface for dev-server
# operations. Do not invoke npm/uvicorn/kill/lsof/tail directly during QA.
#
# 本项目开发模式需要两个进程：
#   backend  — uvicorn app.main:app，端口 8765（生产 docker 仍用 8000）
#   frontend — vite dev server，端口 5173（/api 代理到 8765）
# QA 入口 URL：http://localhost:5173
set -e

cd "$(dirname "$0")/.."

BACKEND_PORT="${QA_BACKEND_PORT:-8765}"  # override via env if 8765 占用
BACKEND_CMD="python -m uvicorn app.main:app --host 0.0.0.0 --port $BACKEND_PORT"
BACKEND_DIR="backend"
FRONTEND_CMD="npm run dev"
FRONTEND_DIR="frontend"
DEV_PORT=5173                  # QA 访问端口（vite，代理 /api）
PID_FILE=".qa-dev-server.pid"  # 两行：backend pid、frontend pid
LOG_FILE=".qa-dev-server.log"
VITE_CFG="frontend/vite.config.ts"
VITE_CFG_BAK="frontend/vite.config.ts.qa.bak"

running() {
  [ -f "$PID_FILE" ] || return 1
  while read -r pid; do
    kill -0 "$pid" 2>/dev/null || return 1
  done < "$PID_FILE"
}

case "${1:-}" in
  start)
    if running; then
      echo "already running (pids: $(tr '\n' ' ' < "$PID_FILE"), port $DEV_PORT). use 'restart' or 'stop' first."
      exit 1
    fi
    rm -f "$PID_FILE"
    : > "$LOG_FILE"
    # Temporarily patch vite proxy target if backend port differs from default 8765.
    if [ "$BACKEND_PORT" != "8765" ] && [ -f "$VITE_CFG" ] && [ ! -f "$VITE_CFG_BAK" ]; then
      cp "$VITE_CFG" "$VITE_CFG_BAK"
      sed -i.tmp "s|http://localhost:8765|http://localhost:$BACKEND_PORT|g" "$VITE_CFG"
      rm -f "$VITE_CFG.tmp"
    fi
    (cd "$BACKEND_DIR" && nohup $BACKEND_CMD >> "../$LOG_FILE" 2>&1 & echo $! > "../$PID_FILE")
    (cd "$FRONTEND_DIR" && nohup $FRONTEND_CMD >> "../$LOG_FILE" 2>&1 & echo $! >> "../$PID_FILE")
    # Wait for both ports to start responding (max ~20s).
    for i in $(seq 1 40); do
      if curl -fsS "http://localhost:$BACKEND_PORT/api/printers" > /dev/null 2>&1 \
        && curl -fsS "http://localhost:$DEV_PORT/" > /dev/null 2>&1; then
        echo "ready (pids: $(tr '\n' ' ' < "$PID_FILE"), url http://localhost:$DEV_PORT, log $LOG_FILE)"
        exit 0
      fi
      sleep 0.5
    done
    echo "did not become ready in 20s; last log lines:"
    tail -n 20 "$LOG_FILE"
    exit 1
    ;;
  stop)
    if [ -f "$PID_FILE" ]; then
      while read -r pid; do
        kill "$pid" 2>/dev/null || true
      done < "$PID_FILE"
      rm -f "$PID_FILE"
      echo "stopped"
    else
      echo "not running (no $PID_FILE)"
    fi
    # Restore vite proxy if it was patched.
    if [ -f "$VITE_CFG_BAK" ]; then
      mv "$VITE_CFG_BAK" "$VITE_CFG"
      echo "restored $VITE_CFG"
    fi
    ;;
  restart)
    "$0" stop
    "$0" start
    ;;
  status)
    if running; then
      echo "running (pids: $(tr '\n' ' ' < "$PID_FILE"), port $DEV_PORT)"
    else
      echo "not running"
      exit 1
    fi
    ;;
  logs)
    N="${2:-20}"
    [ -f "$LOG_FILE" ] && tail -n "$N" "$LOG_FILE" || echo "no log yet"
    ;;
  *)
    echo "usage: $0 {start|stop|restart|status|logs [N]}"
    exit 1
    ;;
esac
