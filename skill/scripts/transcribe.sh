#!/usr/bin/env bash
# 转写一个音频文件（按需服务：没起就自动拉起 → HTTP/socket → 失败落 whisper）
# 用法: transcribe.sh <音频> [语言=zh] [--inproc] [--http] [--whisper]
set -uo pipefail
SDIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ASR_DIR="$HOME/.local/share/pi-asr"
PY="$ASR_DIR/venv/bin/python"
CLI="$ASR_DIR/transcribe_qwen.py"
WPY="$HOME/.local/share/pi-whisper/venv/bin/python"
WCLI="$HOME/.local/share/pi-whisper/transcribe.py"
HTTP="${QWEN_ASR_HTTP_URL:-http://127.0.0.1:8178}"

f=""; lang="zh"; mode="auto"
for a in "$@"; do
  case "$a" in
    --inproc) mode="inproc" ;;
    --http)   mode="http" ;;
    --whisper) mode="whisper" ;;
    -*) ;;
    *) if [ -z "$f" ]; then f="$a"; else lang="$a"; fi ;;
  esac
done
[ -n "$f" ] || { echo "用法: transcribe.sh <音频> [语言=zh] [--inproc|--http|--whisper]" >&2; exit 2; }
[ -f "$f" ] || { echo "文件不存在: $f" >&2; exit 2; }

if [ "$mode" = "whisper" ]; then
  exec "$WPY" "$WCLI" --file "$f" --lang "$lang"
fi

# --http 显式要求走 HTTP：先确保服务在跑（systemd 按需拉起）
if [ "$mode" = "http" ] && ! curl -s -m 2 "$HTTP/health" >/dev/null 2>&1; then
  [ -x "$SDIR/service.sh" ] && "$SDIR/service.sh" ensure >&2 || true
fi

if [ "$mode" = "http" ] || { [ "$mode" = "auto" ] && curl -s -m 2 "$HTTP/health" >/dev/null 2>&1; }; then
  out=$(curl -s -m 300 -F "file=@$f" -F "language=$lang" -F "response_format=text" "$HTTP/v1/audio/transcriptions" 2>/dev/null)
  if [ -n "$out" ] && ! printf '%s' "$out" | grep -q '"error"'; then
    printf '%s\n' "$out"; exit 0
  fi
  [ "$mode" = "http" ] && { echo "HTTP 调用失败: $out" >&2; exit 1; }
fi

args=(--file "$f" --lang "$lang")
[ "$mode" = "inproc" ] && args+=(--no-server)
out=$("$PY" "$CLI" "${args[@]}" 2>/tmp/local-asr-cli.err)
rc=$?
if [ $rc -eq 0 ] && [ -n "$out" ]; then printf '%s\n' "$out"; exit 0; fi

echo "Qwen 转写失败(rc=$rc)，落 whisper 兜底" >&2
tail -2 /tmp/local-asr-cli.err >&2
exec "$WPY" "$WCLI" --file "$f" --lang "$lang"
