#!/bin/bash
# 电力资讯聚合平台 · 一键启动（后台常驻，关闭终端不影响）
# 用法：./start.sh
cd "$(dirname "$0")"

PORT=12333
PID_FILE=.server.pid
LOG_FILE=server.log

# 已在运行则直接提示
if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "✅ 服务已在运行中 (PID $(cat "$PID_FILE"))，无需重复启动"
else
  # 端口被其他进程占用（例如旧的只监听 localhost 的进程）
  if lsof -iTCP:$PORT -sTCP:LISTEN -P >/dev/null 2>&1; then
    echo "⚠️  端口 $PORT 已被占用，可能是旧的服务进程。请先执行 ./stop.sh 再启动。"
    exit 1
  fi
  echo "🚀 正在启动…"
  HOST=0.0.0.0 PORT=$PORT nohup .venv/bin/python app.py >> "$LOG_FILE" 2>&1 &
  echo $! > "$PID_FILE"
  disown
  sleep 2
  if ! kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "❌ 启动失败，请查看日志：tail -20 $LOG_FILE"
    rm -f "$PID_FILE"
    exit 1
  fi
  echo "✅ 启动成功 (PID $(cat "$PID_FILE"))，已转入后台，关闭终端不影响运行"
fi

IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || hostname)
echo ""
echo "   本机访问:   http://127.0.0.1:$PORT"
echo "   局域网访问: http://$IP:$PORT   ← 把这个地址发给同事"
echo ""
echo "   查看日志:   tail -f $LOG_FILE"
echo "   停止服务:   ./stop.sh"
