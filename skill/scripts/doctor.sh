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
case "$st" in active) p "服务 active";; *) w "服务 $st（按需模式也正常，首用会慢 ~15s）";; esac
case "$en" in enabled) p "开机自启已开";; *) w "未开机自启（systemctl --user enable --now qwen-asr）";; esac

echo "[2] 进程与接口"
pgrep -f "qwen_asr_ser""ver.py" >/dev/null 2>&1 && p "服务进程在跑 (pid $(pgrep -f "qwen_asr_ser""ver.py" | head -1))" || w "服务进程未跑"
sock="$ASR_DIR/run/qwen-asr.sock"
[ -S "$sock" ] && p "unix socket 存在" || w "socket 不存在（未启动或已空闲退出）"
h=$(curl -s -m 3 "$HTTP/health" 2>/dev/null || true)
if [ -n "$h" ]; then
  p "HTTP /health: $h"
  q=$(printf '%s' "$h" | grep -o '"quant": *[^,}]*' | cut -d: -f2 | tr -d ' "')
  d=$(printf '%s' "$h" | grep -o '"dev": *[^,}]*' | cut -d: -f2 | tr -d ' "')
  [ "$d" = "cpu" ] && w "当前跑在 CPU（显存不足）→ 热调用约 16s" || p "设备=$d 量化=${q:-bf16}"
else
  w "HTTP 无响应（可能未开 HTTP 或服务未起）"
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
