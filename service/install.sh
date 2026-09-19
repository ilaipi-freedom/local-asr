#!/usr/bin/env bash
# 安装/修复本地 ASR 服务（幂等）。Linux + systemd --user。
# 用法: install.sh [--no-telegram] [--no-whisper] [--no-service]
set -uo pipefail
SVC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ASR_DIR="$HOME/.local/share/pi-asr"
W_DIR="$HOME/.local/share/pi-whisper"
MODEL="${QWEN_ASR_MODEL:-Qwen/Qwen3-ASR-1.7B-hf}"
TG="$HOME/.pi/agent/telegram.json"
DO_TG=1; DO_W=1; DO_SVC=1
for a in "$@"; do case "$a" in
  --no-telegram) DO_TG=0;; --no-whisper) DO_W=0;; --no-service) DO_SVC=0;; esac; done
step(){ printf '\n\033[36m== %s\033[0m\n' "$1"; }
die(){ printf '\033[31m%s\033[0m\n' "$1" >&2; exit 1; }

step "1/6 检查依赖"
for c in python3 ffmpeg; do command -v "$c" >/dev/null || die "缺 $c（apt install ffmpeg python3-venv）"; done
python3 -c 'import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)' || die "需要 Python ≥3.10"

step "2/6 venv 与依赖（首次约 3GB）"
mkdir -p "$ASR_DIR"
[ -x "$ASR_DIR/venv/bin/python" ] || python3 -m venv "$ASR_DIR/venv" || die "venv 创建失败"
"$ASR_DIR/venv/bin/pip" -q install --upgrade pip
"$ASR_DIR/venv/bin/pip" -q install "transformers>=5.13.0" torch torchaudio soundfile accelerate librosa bitsandbytes || die "pip 安装失败"

step "3/6 部署运行脚本"
cp -f "$SVC_DIR"/*.py "$ASR_DIR"/ || die "复制脚本失败"
chmod +x "$ASR_DIR"/transcribe_qwen.py "$ASR_DIR"/qwen_asr_server.py
if [ "$DO_W" = 1 ]; then
  mkdir -p "$W_DIR"
  [ -f "$SVC_DIR/whisper/transcribe.py" ] && cp -f "$SVC_DIR/whisper/transcribe.py" "$W_DIR/"
  if [ ! -x "$W_DIR/venv/bin/python" ]; then
    python3 -m venv "$W_DIR/venv" && "$W_DIR/venv/bin/pip" -q install faster-whisper \
      || echo "⚠️ whisper 兜底安装失败（可选）"
  fi
fi

step "4/6 下载模型 $MODEL"
"$ASR_DIR/venv/bin/hf" download "$MODEL" --quiet || die "模型下载失败"

step "5/6 systemd 用户服务"
if [ "$DO_SVC" = 1 ]; then
  mkdir -p "$HOME/.config/systemd/user"
  cp -f "$SVC_DIR/systemd/qwen-asr.service" "$HOME/.config/systemd/user/"
  systemctl --user daemon-reload
  systemctl --user enable qwen-asr >/dev/null 2>&1
  systemctl --user restart qwen-asr 2>/dev/null || systemctl --user start qwen-asr
  sleep 2
  echo "  服务: $(systemctl --user is-active qwen-asr) / 自启: $(systemctl --user is-enabled qwen-asr)"
else
  echo "  跳过（--no-service）"
fi

step "6/6 接进 pi-telegram"
if [ "$DO_TG" = 1 ] && [ -f "$TG" ]; then
  cp -f "$TG" "$TG.bak-install-$(date +%Y%m%d-%H%M%S)"
  python3 - "$TG" "$ASR_DIR" "$W_DIR" <<'PY'
import collections, json, sys
tg, asr, w = sys.argv[1], sys.argv[2], sys.argv[3]
d = json.load(open(tg), object_pairs_hook=collections.OrderedDict)
def h(label, sel, exe, script):
    o = collections.OrderedDict(label=label); o.update(sel)
    o["template"] = [exe, script, "--file", "{file}", "--lang", "{lang=zh}"]
    o["timeout"] = 300000
    return o
hs = [h("qwen3-asr-1.7b", {"type": "voice"}, asr+"/venv/bin/python", asr+"/transcribe_qwen.py"),
      h("local-whisper-fallback", {"type": "voice"}, w+"/venv/bin/python", w+"/transcribe.py"),
      h("qwen3-asr-1.7b", {"mime": "audio/*"}, asr+"/venv/bin/python", asr+"/transcribe_qwen.py"),
      h("local-whisper-fallback", {"mime": "audio/*"}, w+"/venv/bin/python", w+"/transcribe.py")]
out = collections.OrderedDict(inboundHandlers=hs)
for k, v in d.items():
    if k != "inboundHandlers": out[k] = v
json.dump(out, open(tg, "w"), ensure_ascii=False, indent="\t"); open(tg, "a").write("\n")
print("  已写入 inboundHandlers（原文件已备份）")
PY
  echo "  ⚠️ 需要重启 pi 才生效"
else
  echo "  跳过"
fi
echo
echo "完成。自检: $(dirname "$SVC_DIR")/skill/scripts/doctor.sh"
