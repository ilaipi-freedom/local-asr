#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Telegram 语音 → 文字（本地 faster-whisper，无需联网 API）

用法:  transcribe.py <音频文件> [语言]
环境变量:
  PI_WHISPER_MODEL   模型大小 (默认 small；可选 base/tiny/medium)
  PI_WHISPER_LANG    语言 (默认 zh；置空则自动检测)
  PI_WHISPER_PROMPT  初始提示词(用于偏向专业术语)
  PI_WHISPER_BEAM    beam size (默认 5)
输出: 纯转写文本到 stdout；失败时非 0 退出并打印原因到 stderr（bridge 会回退为普通文件引用）
"""
import os
import sys

DEFAULT_PROMPT = "以下是与机器视觉检测、工业相机、工位、开口、ROI、角度测量、竖直方向、水平方向、边线、圆心、拟合圆、描边相关的技术讨论。"


def parse_args(argv):
    """同时支持  --file X --lang zh   和  位置参数 X zh"""
    path = None
    lang = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--file", "-f", "--audio"):
            i += 1
            path = argv[i] if i < len(argv) else None
        elif a.startswith("--file="):
            path = a.split("=", 1)[1]
        elif a in ("--lang", "-l", "--language"):
            i += 1
            lang = argv[i] if i < len(argv) else None
        elif a.startswith("--lang=") or a.startswith("--language="):
            lang = a.split("=", 1)[1]
        elif a.startswith("-"):
            pass  # 忽略未知开关
        elif path is None:
            path = a
        elif lang is None:
            lang = a
        i += 1
    return path, lang


def main() -> int:
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print("usage: transcribe.py --file <audiofile> [--lang zh]", file=sys.stderr)
        return 2
    path, lang_arg = parse_args(argv)
    lang = lang_arg if lang_arg is not None else os.environ.get("PI_WHISPER_LANG", "zh")
    size = os.environ.get("PI_WHISPER_MODEL", "small")
    prompt = os.environ.get("PI_WHISPER_PROMPT", DEFAULT_PROMPT)
    beam = int(os.environ.get("PI_WHISPER_BEAM", "5") or "5")
    if not path or not os.path.exists(path):
        print("audio file not found: %s" % path, file=sys.stderr)
        return 3
    try:
        from faster_whisper import WhisperModel
    except Exception as exc:  # pragma: no cover
        print("faster-whisper unavailable: %s" % exc, file=sys.stderr)
        return 4
    try:
        model = WhisperModel(size, device="cpu", compute_type="int8")
        segments, _info = model.transcribe(
            path,
            language=(lang or None),
            beam_size=beam,
            vad_filter=True,
            initial_prompt=(prompt or None),
            condition_on_previous_text=False,
        )
        text = "".join(seg.text for seg in segments).strip()
    except Exception as exc:
        print("transcription failed: %s" % exc, file=sys.stderr)
        return 5
    if not text:
        print("empty transcript", file=sys.stderr)
        return 1
    sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
