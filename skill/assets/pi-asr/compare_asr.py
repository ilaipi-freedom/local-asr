#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Qwen3-ASR vs faster-whisper 对比（同一段音频）

用法:  compare_asr.py <音频文件> [语言]
  语言: zh / Chinese  (默认 zh，用于 whisper 与 Qwen 的强制语言提示)

环境变量:
  QWEN_MODELS   逗号分隔的模型列表，默认 "Qwen/Qwen3-ASR-0.6B-hf,Qwen/Qwen3-ASR-1.7B-hf"
  QWEN_DEVICE   auto/cuda/cpu（默认 auto：按显存自动挑）
  WHISPER_PY    pi-whisper 的解释器路径（用于跑 whisper 基线）
  WHISPER_SCRIPT pi-whisper 的 transcribe.py 路径
  WHISPER_MODEL whisper 模型大小（默认 small）
"""
import os
import subprocess
import sys
import tempfile
import time

WHISPER_PY = os.environ.get("WHISPER_PY", "/home/billy/.local/share/pi-whisper/venv/bin/python")
WHISPER_SCRIPT = os.environ.get("WHISPER_SCRIPT", "/home/billy/.local/share/pi-whisper/transcribe.py")
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "small")
QWEN_MODELS = os.environ.get("QWEN_MODELS", "Qwen/Qwen3-ASR-0.6B-hf,Qwen/Qwen3-ASR-1.7B-hf").split(",")
QWEN_DEVICE = os.environ.get("QWEN_DEVICE", "auto")
LANG = sys.argv[2] if len(sys.argv) > 2 else "zh"
QWEN_PROMPT = os.environ.get("QWEN_PROMPT", "词汇：ROI、竖直方向、开口左边线、拟合圆、圆心、外圆、内圆、六块、描边、夹角、工位、偏移、治具、产品、Cobetter。")
LANG_EN = {"zh": "Chinese", "en": "English", "yue": "Cantonese"}.get(LANG, LANG)


def to_wav(src: str) -> str:
    """统一转成 16k 单声道 wav（Qwen 与 whisper 都用同一份）"""
    out = os.path.join(tempfile.gettempdir(), "asr_cmp_16k.wav")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-ar", "16000", "-ac", "1", out], check=True)
    return out


def run_whisper(wav: str):
    env = dict(os.environ, PI_WHISPER_MODEL=WHISPER_MODEL, PI_WHISPER_LANG=LANG)
    t0 = time.time()
    p = subprocess.run([WHISPER_PY, WHISPER_SCRIPT, "--file", wav, "--lang", LANG],
                       capture_output=True, text=True, env=env, timeout=900)
    dt = time.time() - t0
    return (p.stdout.strip() or "(失败: %s)" % p.stderr.strip()[:200]), dt


def run_qwen(model_id: str, wav: str, device: str):
    import torch
    from transformers import AutoModelForMultimodalLM, AutoProcessor
    t0 = time.time()
    proc = AutoProcessor.from_pretrained(model_id)
    t_load0 = time.time()
    if device == "auto":
        dev = "cuda" if torch.cuda.is_available() else "cpu"
    else:
        dev = device
    if dev == "cuda":
        import re
        free, _total = torch.cuda.mem_get_info()
        m = re.search(r"(\d+(?:\.\d+)?)B", model_id.split("/")[-1])
        need_gb = (float(m.group(1)) if m else 2.0) * 2.0 + 0.6  # bf16 权重 + 余量
        if free / 1e9 < need_gb:
            print("      [显存不足: 空闲 %.2fGB < 需要 %.2fGB → 转 CPU]" % (free / 1e9, need_gb))
            dev = "cpu"
    kwargs = {"torch_dtype": torch.bfloat16} if dev == "cuda" else {"torch_dtype": torch.float32}
    try:
        model = AutoModelForMultimodalLM.from_pretrained(model_id, **kwargs)
        model = model.to(dev).eval()
    except Exception as exc:
        if dev == "cuda":
            print("      [GPU 加载失败(%s) → 转 CPU]" % str(exc)[:60])
            torch.cuda.empty_cache()
            dev = "cpu"; kwargs = {"torch_dtype": torch.float32}
            model = AutoModelForMultimodalLM.from_pretrained(model_id, **kwargs)
            model = model.to(dev).eval()
        else:
            raise
    t_load = time.time() - t_load0
    t1 = time.time()
    inputs = proc.apply_transcription_request(audio=wav, language=LANG_EN, prompt=QWEN_PROMPT or None).to(model.device, model.dtype)
    with torch.inference_mode():
        out = model.generate(**inputs, max_new_tokens=256)
    gen = out[:, inputs["input_ids"].shape[1]:]
    text = proc.decode(gen, return_format="transcription_only")[0]
    lang = None
    try:
        lang = proc.decode(gen, return_format="parsed")[0].get("language")
    except Exception:
        pass
    dt = time.time() - t1
    del model
    if dev == "cuda":
        torch.cuda.empty_cache()
    return text, dt, t_load, dev, lang


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    src = sys.argv[1]
    if not os.path.exists(src):
        print("no such file: %s" % src, file=sys.stderr)
        return 2
    wav = to_wav(src)
    print("音频: %s -> %s" % (src, wav))
    try:
        import soundfile as sf
        info = sf.info(wav)
        print("时长 %.1f s / %d Hz / %d ch" % (info.duration, info.samplerate, info.channels))
    except Exception:
        pass
    print("\n%-34s %8s  %s" % ("模型", "耗时", "转写结果"))
    print("-" * 120)
    txt, dt = run_whisper(wav)
    print("%-34s %7.2fs  %s" % ("whisper-%s (CPU)" % WHISPER_MODEL, dt, txt))
    results = {"whisper-%s" % WHISPER_MODEL: txt}
    for mid in QWEN_MODELS:
        mid = mid.strip()
        if not mid:
            continue
        try:
            text, dt, t_load, dev, lang = run_qwen(mid, wav, QWEN_DEVICE)
            print("%-34s %7.2fs  %s" % ("%s (%s)" % (mid.split("/")[-1], dev), dt, text))
            print("%-34s %7.2fs  [加载 %.1fs, 语言=%s]" % ("", 0, t_load, lang))
            results[mid] = text
        except Exception as exc:
            print("%-34s %8s  ERR %s" % (mid.split("/")[-1], "-", str(exc)[:160]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
