#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Qwen3-ASR 常驻服务（unix socket）

模型只加载一次，之后每条语音只需 ~1-3s（省掉 4GB 权重的重复加载）。

用法:  qwen_asr_server.py [--sock PATH] [--idle 秒] [--device auto|cuda|cpu]
协议:  一行一个 JSON
       请求  {"file": "/path/a.ogg", "lang": "zh", "prompt": "词汇：..."}
             {"cmd": "ping"}  /  {"cmd": "shutdown"}
       响应  {"ok": true, "text": "...", "ms": 123, "dev": "cuda", "quant": null}
             {"ok": false, "error": "..."}

由客户端 transcribe_qwen.py 自动拉起；空闲超过 --idle 秒自动退出（释放显存）。
"""
import os

os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

import argparse  # noqa: E402
import email  # noqa: E402
import json  # noqa: E402
import threading  # noqa: E402
import re  # noqa: E402
import signal  # noqa: E402
import socket  # noqa: E402
import subprocess  # noqa: E402
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
import time  # noqa: E402

DEFAULT_SOCK = os.environ.get("QWEN_ASR_SOCK",
                              os.path.join(os.path.expanduser("~"), ".local/share/pi-asr/run/qwen-asr.sock"))
LANG_MAP = {"zh": "Chinese", "en": "English", "yue": "Cantonese", "ja": "Japanese", "ko": "Korean"}
WAV = os.path.join(tempfile.gettempdir(), "qwen_asr_server_16k.wav")


def log(msg: str) -> None:
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), file=sys.stderr, flush=True)


HEADROOM_GB = float(os.environ.get("QWEN_ASR_HEADROOM", "1.2"))


def plan_attempts(torch, want: str, need_bf16: float):
    """常驻服务要"留余量"，别把显存吃干（否则桌面程序/其它任务会 OOM）"""
    if want == "cpu" or not torch.cuda.is_available():
        return [("cpu", None)]
    free = torch.cuda.mem_get_info()[0] / 1e9
    out = []
    if want in ("auto", "bf16") and free >= need_bf16 + HEADROOM_GB:
        out.append(("cuda", None))
    if want in ("auto", "8bit") and free >= 2.4 + HEADROOM_GB:
        out.append(("cuda", "8bit"))
    if want == "cuda" and not out:
        out.append(("cuda", "8bit"))
    if not out:
        log("显存不足(空闲 %.1fGB) → 服务走 CPU" % free)
    out.append(("cpu", None))
    return out


class Engine:
    def __init__(self, model_id: str, device: str):
        import torch
        from transformers import AutoModelForMultimodalLM, AutoProcessor
        self.torch = torch
        self.model_id = model_id
        m = re.search(r"(\d+(?:\.\d+)?)B", model_id.split("/")[-1])
        need_bf16 = (float(m.group(1)) if m else 2.0) * 2.0 + 0.4
        self.proc = AutoProcessor.from_pretrained(model_id)
        self.model = None
        self.lock = __import__("threading").Lock()
        for dev, quant in plan_attempts(torch, device, need_bf16):
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
                self.model = mdl.eval()
                self.dev, self.quant = dev, quant
                log("模型就绪: %s%s" % (dev, "/" + quant if quant else ""))
                break
            except Exception as exc:
                log("加载失败 %s%s: %s" % (dev, "/" + quant if quant else "", str(exc)[:90]))
                if dev == "cuda":
                    torch.cuda.empty_cache()
        if self.model is None:
            raise RuntimeError("所有设备都加载失败")

    def transcribe(self, path: str, lang: str, prompt: str, max_tok: int = 256):
        with self.lock:                      # 串行化：避免并发推理打爆显存
            return self._transcribe(path, lang, prompt, max_tok)

    def _transcribe(self, path: str, lang: str, prompt: str, max_tok: int = 256):
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", path,
                        "-ar", "16000", "-ac", "1", WAV], check=True)
        inputs = self.proc.apply_transcription_request(
            audio=WAV, language=LANG_MAP.get(lang, lang or None), prompt=prompt or None,
        ).to(self.model.device, self.model.dtype)
        with self.torch.inference_mode():
            out = self.model.generate(**inputs, max_new_tokens=max_tok)
        gen = out[:, inputs["input_ids"].shape[1]:]
        return (self.proc.decode(gen, return_format="transcription_only")[0] or "").strip()


def handle(engine, req: dict) -> dict:
    if req.get("cmd") == "ping":
        return {"ok": True, "ready": True, "dev": engine.dev, "quant": engine.quant}
    if req.get("cmd") == "shutdown":
        return {"ok": True, "bye": True}
    path = req.get("file")
    if not path or not os.path.exists(path):
        return {"ok": False, "error": "file not found: %s" % path}
    t0 = time.time()
    try:
        text = engine.transcribe(path, req.get("lang") or "zh", req.get("prompt") or "",
                                 int(req.get("max_tokens") or 256))
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:300]}
    if not text:
        return {"ok": False, "error": "empty transcript"}
    return {"ok": True, "text": text, "ms": int((time.time() - t0) * 1000),
            "dev": engine.dev, "quant": engine.quant}


# ---------------- HTTP 接口（OpenAI 兼容，可选） ----------------

class _Handler(BaseHTTPRequestHandler):
    engine = None
    token = None
    touch = None
    server_version = "qwen3-asr/1.0"

    def log_message(self, fmt, *args):        # 日志走 stderr
        log("http %s" % (fmt % args))

    def _send(self, code, obj, ctype="application/json"):
        body = obj if isinstance(obj, bytes) else json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _auth_ok(self):
        if not self.token:
            return True
        hdr = self.headers.get("Authorization", "")
        return hdr == "Bearer " + self.token

    def do_GET(self):
        if not self._auth_ok():
            return self._send(401, {"error": {"message": "unauthorized"}})
        path = self.path.split("?")[0]
        if path in ("/health", "/v1/health"):
            return self._send(200, {"ok": True, "dev": self.engine.dev, "quant": self.engine.quant,
                                    "model": self.engine.model_id})
        if path in ("/v1/models", "/models"):
            return self._send(200, {"object": "list", "data": [
                {"id": "qwen3-asr-1.7b", "object": "model", "owned_by": "qwen"}]})
        return self._send(404, {"error": {"message": "not found: %s" % path}})

    def do_POST(self):
        if not self._auth_ok():
            return self._send(401, {"error": {"message": "unauthorized"}})
        path = self.path.split("?")[0]
        if path not in ("/v1/audio/transcriptions", "/audio/transcriptions"):
            return self._send(404, {"error": {"message": "not found: %s" % path}})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n) if n else b""
            ctype = self.headers.get("Content-Type", "")
            fields, filename, blob = {}, None, None
            if "multipart/form-data" in ctype:
                msg = email.message_from_bytes(
                    b"Content-Type: " + ctype.encode() + b"\r\nMIME-Version: 1.0\r\n\r\n" + body)
                for part in msg.walk():
                    if part.get_content_maintype() == "multipart":
                        continue
                    data = part.get_payload(decode=True) or b""
                    name = part.get_param("name", header="content-disposition") or ""
                    fn = part.get_filename()
                    if fn:
                        filename, blob = fn, data
                    elif name:
                        fields[name] = data.decode("utf-8", "replace")
            else:                                   # 直接 POST 音频字节
                fields = {}
                filename, blob = "audio.bin", body
            if not blob:
                return self._send(400, {"error": {"message": "missing audio file"}})
            lang = fields.get("language") or "zh"
            prompt = fields.get("prompt") or ""
            fmt = fields.get("response_format") or "json"
            ext = os.path.splitext(filename or "")[1] or ".ogg"
            tmp = os.path.join(tempfile.gettempdir(), "qwen_asr_http" + ext)
            with open(tmp, "wb") as fh:
                fh.write(blob)
            t0 = time.time()
            text = self.engine.transcribe(tmp, lang, prompt, 256)
            if self.touch:
                self.touch()
            if not text:
                return self._send(200, {"text": ""})
            if fmt == "text":
                return self._send(200, text.encode("utf-8"), "text/plain; charset=utf-8")
            return self._send(200, {"text": text, "model": "qwen3-asr-1.7b",
                                    "language": lang, "ms": int((time.time() - t0) * 1000)})
        except Exception as exc:
            return self._send(500, {"error": {"message": str(exc)[:300]}})


def start_http(engine, addr: str, token: str, touch):
    host, _, port = addr.rpartition(":")
    host = host or "127.0.0.1"
    cls = type("H", (_Handler,), {"engine": engine, "token": token or None, "touch": staticmethod(touch)})
    httpd = ThreadingHTTPServer((host, int(port)), cls)
    httpd.daemon_threads = True
    t = threading.Thread(target=httpd.serve_forever, name="http", daemon=True)
    t.start()
    log("HTTP 接口: http://%s:%s/v1/audio/transcriptions%s" % (host, port, "  (需 token)" if token else ""))
    return httpd


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sock", default=DEFAULT_SOCK)
    ap.add_argument("--idle", type=int, default=int(os.environ.get("QWEN_ASR_IDLE", "1800")))
    ap.add_argument("--device", default=os.environ.get("QWEN_ASR_DEVICE", "auto"),
                    help="auto/cuda/cpu；auto 会在留出 %sGB 余量后选 bf16 或 8bit" % HEADROOM_GB)
    ap.add_argument("--model", default=os.environ.get("QWEN_ASR_MODEL", "Qwen/Qwen3-ASR-1.7B-hf"))
    ap.add_argument("--http", default=os.environ.get("QWEN_ASR_HTTP", ""),
                    help="HTTP 监听地址，如 127.0.0.1:8178（默认关闭）")
    ap.add_argument("--token", default=os.environ.get("QWEN_ASR_TOKEN", ""),
                    help="HTTP Bearer token（对外暴露时务必设置）")
    a = ap.parse_args()

    os.makedirs(os.path.dirname(a.sock), exist_ok=True)
    engine = Engine(a.model, a.device)

    if os.path.exists(a.sock):
        try:
            os.unlink(a.sock)
        except OSError:
            pass
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(a.sock)
    os.chmod(a.sock, 0o600)
    srv.listen(8)
    log("监听 %s (空闲 %ds 后退出)" % (a.sock, a.idle))

    stop = {"v": False}

    def bye(*_a):
        stop["v"] = True
    signal.signal(signal.SIGTERM, bye)
    signal.signal(signal.SIGINT, bye)

    state = {"last": time.time()}

    def touch():
        state["last"] = time.time()

    if a.http:
        start_http(engine, a.http, a.token, touch)

    srv.settimeout(5.0)
    last = time.time()
    while not stop["v"]:
        try:
            conn, _ = srv.accept()
        except socket.timeout:
            if a.idle > 0 and time.time() - state["last"] > a.idle:
                log("空闲超时，退出")
                break
            continue
        except OSError:
            break
        touch()
        try:
            conn.settimeout(300.0)
            buf = b""
            while b"\n" not in buf:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                buf += chunk
            if not buf:
                continue
            req = json.loads(buf.split(b"\n", 1)[0].decode("utf-8", "replace"))
            resp = handle(engine, req)
            conn.sendall((json.dumps(resp, ensure_ascii=False) + "\n").encode("utf-8"))
            if resp.get("bye"):
                stop["v"] = True
        except Exception as exc:
            try:
                conn.sendall((json.dumps({"ok": False, "error": str(exc)[:300]}) + "\n").encode("utf-8"))
            except Exception:
                pass
        finally:
            try:
                conn.close()
            except Exception:
                pass
        touch()

    try:
        srv.close()
    finally:
        if os.path.exists(a.sock):
            try:
                os.unlink(a.sock)
            except OSError:
                pass
    log("已退出")
    return 0


if __name__ == "__main__":
    sys.exit(main())
