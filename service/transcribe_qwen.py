#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Telegram 语音 → 文字（Qwen3-ASR-1.7B，本地）

优先走常驻服务（unix socket，模型只加载一次 → 每条 ~1~3s）；
服务没起就自动拉起；服务不可用则退化为"进程内加载"（慢但不丢消息）。

用法:  transcribe_qwen.py --file <音频> [--lang zh] [--prompt "..."] [--device auto|cuda|cpu]
输出:  纯转写文本到 stdout；失败非 0 退出（stderr 写原因，bridge 会回退到下一个 handler）

环境变量:
  QWEN_ASR_SERVER  1/0    是否使用常驻服务（默认 1）
  QWEN_ASR_SOCK    路径   socket 路径（默认 ~/.local/share/pi-asr/run/qwen-asr.sock）
  QWEN_ASR_STARTUP 秒     首次拉起服务的等待上限（默认 150）
  QWEN_ASR_IDLE    秒     服务空闲退出时间（默认 1800）
  QWEN_ASR_MODEL / QWEN_ASR_PROMPT / QWEN_ASR_LANG / QWEN_ASR_DEVICE / QWEN_ASR_MAXTOK
"""
import os

os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

import fcntl  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
import socket  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
import time  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(HERE, "qwen_asr_server.py")
DEFAULT_SOCK = os.environ.get("QWEN_ASR_SOCK", os.path.join(HERE, "run", "qwen-asr.sock"))
DEFAULT_MODEL = os.environ.get("QWEN_ASR_MODEL", "Qwen/Qwen3-ASR-1.7B-hf")
DEFAULT_PROMPT = os.environ.get(
    "QWEN_ASR_PROMPT",
    "词汇：ROI、竖直方向、水平方向、开口、开口左边线、拟合圆、圆心、外圆、内圆、六块、描边、"
    "夹角、工位、偏移、治具、产品、膜、焊线、像素、亚像素、Cobetter。",
)
LANG_MAP = {"zh": "Chinese", "en": "English", "yue": "Cantonese", "ja": "Japanese", "ko": "Korean"}


def note(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def parse_args(argv):
    out = {"file": None, "lang": None, "prompt": None, "device": None, "model": None, "server": None}
    keys = {"--file": "file", "-f": "file", "--audio": "file",
            "--lang": "lang", "-l": "lang", "--language": "lang",
            "--prompt": "prompt", "--context": "prompt",
            "--device": "device", "--model": "model"}
    i = 0
    while i < len(argv):
        a = argv[i]
        key = a.split("=", 1)[0]
        if key in keys:
            name = keys[key]
            if "=" in a:
                out[name] = a.split("=", 1)[1]
            else:
                i += 1
                out[name] = argv[i] if i < len(argv) else None
        elif a in ("--no-server",):
            out["server"] = "0"
        elif a.startswith("-"):
            pass
        elif out["file"] is None:
            out["file"] = a
        i += 1
    return out


def to_wav(src: str) -> str:
    out = os.path.join(tempfile.gettempdir(), "qwen_asr_16k.wav")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", src,
                    "-ar", "16000", "-ac", "1", out], check=True)
    return out


# ---------- 常驻服务客户端 ----------

def sock_call(sock_path: str, req: dict, timeout: float = 300.0):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(sock_path)
        s.sendall((json.dumps(req, ensure_ascii=False) + "\n").encode("utf-8"))
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    finally:
        s.close()
    if not buf:
        raise RuntimeError("empty response from server")
    return json.loads(buf.split(b"\n", 1)[0].decode("utf-8", "replace"))


def ping(sock_path: str) -> bool:
    if not os.path.exists(sock_path):
        return False
    try:
        r = sock_call(sock_path, {"cmd": "ping"}, timeout=3.0)
        return bool(r.get("ready"))
    except Exception:
        return False


def ensure_server(sock_path: str, startup: float) -> bool:
    if ping(sock_path):
        return True
    os.makedirs(os.path.dirname(sock_path), exist_ok=True)
    lock_path = sock_path + ".lock"
    with open(lock_path, "w") as lf:
        try:
            fcntl.flock(lf, fcntl.LOCK_EX)
        except Exception:
            pass
        if ping(sock_path):          # 别人已经拉起来了
            return True
        if os.path.exists(sock_path):
            try:
                os.unlink(sock_path)  # 清理残留 socket
            except OSError:
                pass
        logf = open(sock_path + ".log", "ab", buffering=0)
        subprocess.Popen([sys.executable, SERVER, "--sock", sock_path],
                         stdout=logf, stderr=logf, stdin=subprocess.DEVNULL,
                         start_new_session=True, close_fds=True)
        note("正在拉起常驻服务（首次需加载模型）...")
        t0 = time.time()
        while time.time() - t0 < startup:
            if ping(sock_path):
                note("常驻服务就绪（%.1fs）" % (time.time() - t0))
                return True
            time.sleep(0.4)
    return False


# ---------- 进程内兜底 ----------

def inprocess(wav: str, lang: str, prompt: str, model_id: str, want: str, max_tok: int):
    import torch
    from transformers import AutoModelForMultimodalLM, AutoProcessor
    m = re.search(r"(\d+(?:\.\d+)?)B", model_id.split("/")[-1])
    need_bf16 = (float(m.group(1)) if m else 2.0) * 2.0 + 0.4
    try:
        if want == "cpu" or not torch.cuda.is_available():
            attempts = [("cpu", None)]
        else:
            free = torch.cuda.mem_get_info()[0] / 1e9
            attempts = []
            if free >= need_bf16:
                attempts.append(("cuda", None))
            if free >= 2.4:
                attempts.append(("cuda", "8bit"))
            if not attempts:
                attempts.append(("cuda", "8bit"))
            attempts.append(("cpu", None))
    except Exception as exc:      # CUDA 上下文拿不到(如显存被占满) → 直接 CPU
        note("CUDA 不可用(%s) → 用 CPU" % str(exc)[:60])
        attempts = [("cpu", None)]
    proc = AutoProcessor.from_pretrained(model_id)
    model = None
    dev = attempts[0][0]
    for dev, quant in attempts:
        try:
            kw = {}
            if dev == "cuda" and quant == "8bit":
                from transformers import BitsAndBytesConfig
                kw["quantization_config"] = BitsAndBytesConfig(load_in_8bit=True)
                kw["device_map"] = "auto"
            elif dev == "cuda":
                kw["dtype"] = torch.bfloat16
            else:
                kw["dtype"] = torch.float32
            mdl = AutoModelForMultimodalLM.from_pretrained(model_id, **kw)
            if not (dev == "cuda" and quant == "8bit"):
                mdl = mdl.to(dev)
            model = mdl.eval()
            break
        except Exception as exc:
            note("加载失败 %s%s: %s" % (dev, "/" + quant if quant else "", str(exc)[:70]))
            if dev == "cuda":
                try:
                    torch.cuda.empty_cache()
                except Exception:
                    pass
    if model is None:
        raise RuntimeError("所有设备都加载失败")
    inputs = proc.apply_transcription_request(
        audio=wav, language=LANG_MAP.get(lang, lang or None), prompt=prompt or None,
    ).to(model.device, model.dtype)
    with torch.inference_mode():
        out = model.generate(**inputs, max_new_tokens=max_tok)
    gen = out[:, inputs["input_ids"].shape[1]:]
    return (proc.decode(gen, return_format="transcription_only")[0] or "").strip(), dev


def main() -> int:
    args = parse_args(sys.argv[1:])
    path = args["file"]
    if not path or path in ("-h", "--help"):
        note("usage: transcribe_qwen.py --file <audio> [--lang zh] [--prompt ...]")
        return 2
    if not os.path.exists(path):
        note("audio file not found: %s" % path)
        return 3
    lang = args["lang"] or os.environ.get("QWEN_ASR_LANG", "zh")
    prompt = args["prompt"] if args["prompt"] is not None else DEFAULT_PROMPT
    model_id = args["model"] or DEFAULT_MODEL
    want = args["device"] or os.environ.get("QWEN_ASR_DEVICE", "auto")
    max_tok = int(os.environ.get("QWEN_ASR_MAXTOK", "256") or "256")
    use_server = (args["server"] or os.environ.get("QWEN_ASR_SERVER", "1")) != "0"
    sock_path = DEFAULT_SOCK
    t0 = time.time()

    try:
        wav = to_wav(path)
    except Exception as exc:
        note("audio decode failed: %s" % exc)
        return 5

    text = ""
    if use_server:
        try:
            if ensure_server(sock_path, float(os.environ.get("QWEN_ASR_STARTUP", "150"))):
                r = sock_call(sock_path, {"file": wav, "lang": lang, "prompt": prompt,
                                          "max_tokens": max_tok})
                if r.get("ok"):
                    text = (r.get("text") or "").strip()
                    note("qwen3-asr[server/%s%s] %.1fs (推理 %.1fs)" % (
                        r.get("dev"), "/" + r["quant"] if r.get("quant") else "",
                        time.time() - t0, (r.get("ms") or 0) / 1000.0))
                else:
                    note("服务返回错误: %s" % str(r.get("error"))[:150])
            else:
                note("常驻服务启动超时 → 转进程内加载")
        except Exception as exc:
            note("服务调用失败(%s) → 转进程内加载" % str(exc)[:100])

    if not text:
        try:
            text, dev = inprocess(wav, lang, prompt, model_id, want, max_tok)
            note("qwen3-asr[in-process/%s] %.1fs" % (dev, time.time() - t0))
        except Exception as exc:
            note("transcription failed: %s" % exc)
            return 6

    if not text:
        note("empty transcript")
        return 1
    sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
