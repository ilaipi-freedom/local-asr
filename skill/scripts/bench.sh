#!/usr/bin/env bash
# 用 samples/ 里的语音跑模型 A/B 对比
# 用法: bench.sh [样本目录] [语言=zh]
set -uo pipefail
D="${1:-$HOME/.local/share/pi-asr/samples}"; LANG_="${2:-zh}"
PY="$HOME/.local/share/pi-asr/venv/bin/python"
CMP="$HOME/.local/share/pi-asr/compare_asr.py"
[ -d "$D" ] || { echo "样本目录不存在: $D" >&2; exit 2; }
n=$(ls "$D"/*.ogg 2>/dev/null | wc -l)
[ "$n" -gt 0 ] || { echo "样本目录里没有 .ogg: $D" >&2; exit 2; }
echo "样本 $n 条，语言=$LANG_，对比 whisper-small(CPU) vs Qwen3-ASR-1.7B"
echo "（提示：Qwen 已常驻服务时，本脚本仍会重新加载模型以公平对比）"
for f in "$D"/*.ogg; do
  echo "──────── $(basename "$f") ────────"
  QWEN_MODELS="Qwen/Qwen3-ASR-1.7B-hf" "$PY" "$CMP" "$f" "$LANG_" 2>/dev/null | grep -v "^$"
done
