#!/usr/bin/env bash
# 服务生命周期管理（按需模式）：ensure / start / stop / restart / status / logs
#   ensure  没跑就拉起来并等就绪（默认动作；转写前调它最省事）
#   start   只 start，不等就绪
#   stop    停掉（systemd + 任何游离进程），释放显存
#   status  人类可读状态（服务/socket/HTTP/显存）
# 用法: service.sh [ensure|start|stop|restart|status|logs]
set -uo pipefail
ASR_DIR="$HOME/.local/share/pi-asr"
SOCK="${QWEN_ASR_SOCK:-$ASR_DIR/run/qwen-asr.sock}"
HTTP="${QWEN_ASR_HTTP_URL:-http://127.0.0.1:8178}"
UNIT="${QWEN_ASR_UNIT:-qwen-asr}"
WAIT="${QWEN_ASR_STARTUP:-150}"
action="${1:-ensure}"

# socket ping（只用系统 python 标准库，不碰 venv/torch）
is_up() {
  python3 - "$SOCK" <<'PY' 2>/dev/null
import json, os, socket, sys
p = sys.argv[1]
if not os.path.exists(p):
    sys.exit(1)
try:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(3)
    s.connect(p)
    s.sendall(b'{"cmd":"ping"}\n')
    buf = b""
    while b"\n" not in buf:
        c = s.recv(4096)
        if not c:
            break
        buf += c
    s.close()
    sys.exit(0 if json.loads(buf.split(b"\n", 1)[0]).get("ready") else 1)
except Exception:
    sys.exit(1)
PY
}

wait_ready() {
  local t0=$SECONDS
  while [ $((SECONDS - t0)) -lt "$WAIT" ]; do
    if is_up; then
      echo "✅ 服务就绪（$((SECONDS - t0))s）  $(curl -s -m 3 "$HTTP/health" 2>/dev/null)"
      return 0
    fi
    sleep 0.5
  done
  echo "❌ 等待 ${WAIT}s 仍未就绪；journalctl --user -u $UNIT -n 50 --no-pager" >&2
  return 1
}

stray_pids() { pgrep -f "qwen_asr_ser""ver.py" 2>/dev/null || true; }

case "$action" in
  ensure|start)
    if is_up; then
      echo "✅ 已在运行 $(curl -s -m 3 "$HTTP/health" 2>/dev/null)"
      exit 0
    fi
    echo "服务未运行 → 按需启动 $UNIT"
    if ! systemctl --user start "$UNIT" 2>/dev/null; then
      echo "⚠️ systemctl 启动失败/不可用，尝试直接拉起（日志 $SOCK.log）"
      mkdir -p "$(dirname "$SOCK")"
      nohup "$ASR_DIR/venv/bin/python" "$ASR_DIR/qwen_asr_server.py" \
        --sock "$SOCK" --http "${HTTP#http://}" --idle "${QWEN_ASR_IDLE:-1800}" \
        >>"$SOCK.log" 2>&1 &
      disown 2>/dev/null || true
    fi
    [ "$action" = start ] && exit 0
    wait_ready
    ;;
  stop)
    systemctl --user stop "$UNIT" 2>/dev/null || true
    sleep 1
    if is_up; then           # 游离进程（不是 systemd 拉起的）
      echo "发现不受 systemd 管理的实例 → 让它自己退出"
      python3 - "$SOCK" <<'PY' 2>/dev/null || true
import socket, sys
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.settimeout(5)
try:
    s.connect(sys.argv[1]); s.sendall(b'{"cmd":"shutdown"}\n'); s.recv(4096)
except Exception:
    pass
finally:
    s.close()
PY
      sleep 2
    fi
    p=$(stray_pids)
    if [ -n "$p" ]; then
      echo "残留进程: $p → kill"
      # shellcheck disable=SC2086
      kill $p 2>/dev/null || true
      sleep 2
    fi
    is_up && { echo "❌ 仍在运行（$(stray_pids)）" >&2; exit 1; }
    echo "⏹  已停止；GPU 空闲显存 $(nvidia-smi --query-gpu=memory.free --format=csv,noheader 2>/dev/null || echo n/a)"
    ;;
  restart)
    systemctl --user daemon-reload 2>/dev/null || true
    systemctl --user restart "$UNIT" 2>/dev/null || { echo "restart 失败" >&2; exit 1; }
    wait_ready
    ;;
  status)
    echo "unit      : $(systemctl --user is-active "$UNIT" 2>/dev/null)/$(systemctl --user is-enabled "$UNIT" 2>/dev/null)"
    echo "socket    : $([ -S "$SOCK" ] && echo 存在 || echo 无)  可ping: $(is_up && echo yes || echo no)"
    echo "HTTP      : $(curl -s -m 3 "$HTTP/health" 2>/dev/null || echo 无响应)"
    p=$(stray_pids); [ -n "$p" ] && echo "进程      : $p" || echo "进程      : 无"
    echo "显存      : $(nvidia-smi --query-gpu=memory.used,memory.free --format=csv,noheader 2>/dev/null || echo n/a)"
    ;;
  logs)
    journalctl --user -u "$UNIT" -n "${2:-50}" --no-pager
    ;;
  *)
    echo "用法: service.sh [ensure|start|stop|restart|status|logs]" >&2; exit 2;;
esac
