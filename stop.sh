#!/bin/bash
# 停止电力资讯聚合平台服务
cd "$(dirname "$0")"

PID_FILE=.server.pid
PORT=12333

stopped=0
if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  kill "$(cat "$PID_FILE")" && echo "✅ 服务已停止 (PID $(cat "$PID_FILE"))"
  stopped=1
else
  # 兼容：端口被脚本外启动的进程占用时，按端口停止
  PID=$(lsof -tiTCP:$PORT -sTCP:LISTEN 2>/dev/null | head -1)
  if [ -n "$PID" ]; then
    kill "$PID" && echo "✅ 已停止占用端口 $PORT 的进程 (PID $PID)"
    stopped=1
  fi
fi
[ "$stopped" = "0" ] && echo "ℹ️  服务未在运行"
rm -f "$PID_FILE"
