#!/usr/bin/env bash
# 把仓库内容部署到系统位置（仓库是唯一来源）
#   service/*.py  → ~/.local/share/pi-asr/          （运行时代码）
#   service/whisper/transcribe.py → ~/.local/share/pi-whisper/
#   skill/        → ~/.agents/skills/local-asr/      （pi skill）
# 用法: sync.sh [--link] [--restart]
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ASR="$HOME/.local/share/pi-asr"; WHIS="$HOME/.local/share/pi-whisper"
SKILL="$HOME/.agents/skills/local-asr"
LINK=0; RESTART=0
for a in "$@"; do case "$a" in --link) LINK=1;; --restart) RESTART=1;; esac; done

echo "== 1/4 同步运行时 → $ASR"
mkdir -p "$ASR" "$WHIS"
# 运行时副本也回填到 skill/assets（保证 skill 自包含）
mkdir -p "$REPO/skill/assets/pi-asr"
cp -f "$REPO"/service/*.py "$REPO/skill/assets/pi-asr/"
[ -f "$ASR/README.md" ] && cp -f "$ASR/README.md" "$REPO/skill/assets/pi-asr/README.md"
cp -f "$REPO"/service/*.py "$ASR/"
cp -f "$REPO"/service/whisper/transcribe.py "$WHIS/transcribe.py"
chmod +x "$ASR"/*.py "$WHIS"/*.py

echo "== 2/4 同步 systemd unit"
mkdir -p "$HOME/.config/systemd/user"
cp -f "$REPO"/service/systemd/qwen-asr.service "$HOME/.config/systemd/user/"
systemctl --user daemon-reload 2>/dev/null || true

echo "== 3/4 部署 skill → $SKILL"
if [ "$LINK" = 1 ]; then
  rm -rf "$SKILL"; ln -s "$REPO/skill" "$SKILL"; echo "   （软链模式：编辑仓库即时生效）"
else
  rm -rf "$SKILL"                      # 干净镜像，避免残留旧文件
  mkdir -p "$SKILL/scripts" "$SKILL/references" "$SKILL/assets/pi-asr"
  cp -f "$REPO/skill/SKILL.md" "$SKILL/"
  [ -f "$REPO/skill/README.md" ] && cp -f "$REPO/skill/README.md" "$SKILL/"
  cp -f "$REPO"/skill/scripts/*.sh "$SKILL/scripts/"
  cp -f "$REPO"/skill/references/*.md "$SKILL/references/"
  cp -f "$REPO"/skill/assets/pi-asr/* "$SKILL/assets/pi-asr/"
  chmod +x "$SKILL"/scripts/*.sh
fi

echo "== 4/4 重启服务"
if [ "$RESTART" = 1 ]; then
  systemctl --user restart qwen-asr && echo "   已重启"
else
  echo "   （未重启；改了服务端代码用 --restart，或手动 systemctl --user restart qwen-asr）"
fi
echo "完成。自检: $SKILL/scripts/doctor.sh"
