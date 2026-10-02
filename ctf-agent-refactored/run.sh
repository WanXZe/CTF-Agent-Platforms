#!/bin/bash
# CTF-Agent 服务控制脚本（VM 内使用）
#   ./run.sh start | stop | restart | status | log [n]
cd "$(dirname "$0")" || exit 1
PORT=12346
PATTERN="main.py --host 0.0.0.0 --port ${PORT}"
case "$1" in
  start)
    if pgrep -f "$PATTERN" > /dev/null; then echo "already running (pid $(pgrep -f "$PATTERN" | tr '\n' ' '))"; exit 0; fi
    nohup python3 main.py --host 0.0.0.0 --port ${PORT} > /tmp/ctf-agent.log 2>&1 &
    sleep 3
    pgrep -f "$PATTERN" > /dev/null && echo "started: http://192.168.174.132:${PORT}  (log: /tmp/ctf-agent.log)" || { echo "start failed:"; tail -20 /tmp/ctf-agent.log; exit 1; }
    ;;
  stop)
    pkill -f "$PATTERN" && echo "stopped" || echo "not running"
    ;;
  restart)
    pkill -f "$PATTERN" > /dev/null 2>&1; sleep 1; exec "$0" start
    ;;
  status)
    pgrep -af "$PATTERN" || echo "not running"
    curl -sS -m 5 "http://127.0.0.1:${PORT}/api/health" && echo
    ;;
  log)
    tail -n "${2:-40}" /tmp/ctf-agent.log
    ;;
  *)
    echo "usage: $0 start|stop|restart|status|log [n]"
    ;;
esac
