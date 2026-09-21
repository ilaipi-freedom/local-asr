#!/usr/bin/env bash
# local-asr 自检：服务/显存/量化/模型/配置/handler 一次查完
set -uo pipefail
ASR_DIR="$HOME/.local/share/pi-asr"
W_DIR="$HOME/.local/share/pi-whisper"
UNIT="$HOME/.config/systemd/user/qwen-asr.service"
TG="$HOME/.pi/agent/telegram.json"
HTTP="${QWEN_ASR_HTTP_URL:-http://127.0.0.1:8178}"
ok=0; warn=0; bad=0
p(){ printf '  \033[32m✅\033[0m %s\n' "$1"; ok=$((ok+1)); }
w(){ printf '  \033[33m⚠️ \033[0m %s\n' "$1"; warn=$((warn+1)); }
x(){ printf '  \033[31m❌\033[0m %s\n' "$1"; bad=$((bad+1)); }

echo "═══ local-asr 自检 ═══"

echo "[1] systemd 服务"
if [ -f "$UNIT" ]; then p "unit 存在: $UNIT"; else x "unit 缺失: $UNIT（跑 scripts/install.sh）"; fi
st=$(systemctl --user is-active qwen-asr 2>/dev/null || true)
en=$(systemctl --user is-enabled qwen-asr 2>/dev/null || true)
idle=$(grep -m1 '^ExecStart=' "$UNIT" 2>/dev/null | grep -o -- '--idle [0-9]*' | awk '{print $2}')
case "$st" in
  active) p "服务 active（按需模式：空闲 ${idle:-1800}s 后自动退出释放显存）";;
  activating) w "服务 activating（正在加载模型）";;
  *) p "服务 $st —— 按需模式（正常；首用自动拉起，冷启动 ~10-15s）";;
esac
case "$en" in
  enabled) w "开机自启已开（会常驻占 ~4GB 显存）；不需要就 systemctl --user disable --now qwen-asr";;
  *) p "开机不自启（按需模式）";;
esac
case "$st" in
  active) ;;
  *) if pgrep -f "qwen_asr_ser""ver.py" >/dev/null 2>&1; then
       w "有服务进程但 systemd 显示 $st —— 可能是客户端直接拉起的游离实例；scripts/service.sh restart 接管"
     fi;;
esac

echo "[2] 进程与接口"
sock="$ASR_DIR/run/qwen-asr.sock"
h=$(curl -s -m 3 "$HTTP/health" 2>/dev/null || true)
pid=$(pgrep -f "qwen_asr_ser""ver.py" 2>/dev/null | head -1)
if [ -n "$pid" ]; then
  p "服务进程在跑 (pid $pid)"
  [ -S "$sock" ] && p "unix socket 存在" || w "socket 不存在（进程在但 socket 未建，看 journalctl）"
  if [ -n "$h" ]; then
    p "HTTP /health: $h"
    q=$(printf '%s' "$h" | grep -o '"quant": *[^,}]*' | cut -d: -f2 | tr -d ' "')
    d=$(printf '%s' "$h" | grep -o '"dev": *[^,}]*' | cut -d: -f2 | tr -d ' "')
    [ "$d" = "cpu" ] && w "当前跑在 CPU（显存不足）→ 热调用约 16s" || p "设备=$d 量化=${q:-bf16}"
  else
    w "HTTP 无响应（进程在但 HTTP 没起？确认 unit 的 --http 参数）"
  fi
else
  p "服务未启动 —— 按需模式（正常）；转写前会自动拉起，或 scripts/service.sh ensure"
  [ -S "$sock" ] && w "socket 文件残留（服务没跑但 socket 在；客户端会自动清理）"
  [ -n "$h" ] && w "HTTP 有响应但找不到服务进程（端口被占？ss -lntp | grep 8178）"
fi

echo "[3] 显存"
if command -v nvidia-smi >/dev/null 2>&1; then
  free=$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits 2>/dev/null | head -1)
  used=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null | head -1)
  echo "     used=${used}MiB free=${free}MiB"
  if [ -n "$free" ] && [ "$free" -lt 2400 ]; then
    if [ "$st" = "active" ] && printf '%s' "${h:-}" | grep -q '"dev": *"cuda"'; then
      p "空闲 ${free}MiB —— 服务已在用 GPU（显存已被它占用），正常"
    else
      x "空闲显存 <2.4GB 且服务未在 GPU 上：冷启动会退到 CPU，建议关掉占显存的桌面程序"
    fi
  else
    p "显存够（8bit 需 2.4GB，bf16 需 4.0GB+余量）"
  fi
else w "无 nvidia-smi（CPU-only 环境）"; fi

echo "[4] 模型与依赖"
for m in models--Qwen--Qwen3-ASR-1.7B-hf; do
  d="$HOME/.cache/huggingface/hub/$m"
  if [ -d "$d" ]; then p "模型缓存存在: $m"; else x "模型缺失: $m（hf download Qwen/Qwen3-ASR-1.7B-hf）"; fi
done
[ -x "$ASR_DIR/venv/bin/python" ] && p "venv 存在" || x "venv 缺失: $ASR_DIR/venv"
[ -f "$ASR_DIR/qwen_asr_server.py" ] && p "服务脚本存在" || x "缺 qwen_asr_server.py"
[ -x "$W_DIR/venv/bin/python" ] && p "whisper 兜底可用" || w "whisper 兜底缺失（可选）"

echo "[5] pi-telegram 配置"
if [ -f "$TG" ]; then
  if python3 -c "import json,sys;json.load(open('$TG'))" 2>/dev/null; then p "telegram.json 合法"; else x "telegram.json 不是合法 JSON"; fi
  python3 - "$TG" "$ASR_DIR" "$W_DIR" <<'PYEOF'
import json,sys,os
tg,asr,w=sys.argv[1],sys.argv[2],sys.argv[3]
try: c=json.load(open(tg))
except Exception as e: print("  ❌ 读取失败:",e); raise SystemExit
hs=c.get("inboundHandlers") or []
if not hs: print("  ❌ 没有 inboundHandlers（语音不会自动转写）"); raise SystemExit
labels=[h.get("label") or h.get("type") or h.get("mime") for h in hs]
print("  ✅ handler 顺序:", " → ".join(map(str,labels)))
first=hs[0].get("template") or []
exe=first[0] if first else ""
if "pi-asr" in str(exe): print("  ✅ 首选是 Qwen3-ASR")
else: print("  ⚠️  首选不是 Qwen（当前: %s）" % exe)
for h in hs:
    t=h.get("template") or []
    if len(t)>=2 and not (os.access(t[0],os.X_OK) and os.path.exists(t[1])):
        print("  ❌ handler 路径无效:", h.get("label"), t[:2])
PYEOF
else x "telegram.json 不存在: $TG"; fi

echo
echo "═══ 汇总: ✅$ok  ⚠️$warn  ❌$bad ═══"
[ $bad -eq 0 ] && echo "基本健康。若刚改过 telegram.json，记得重启 pi 才生效。" || echo "有 ❌ 项需要修；多数可用 scripts/install.sh 修好。"
