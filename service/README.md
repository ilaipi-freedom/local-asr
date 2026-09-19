# service —— 本地语音识别服务

常驻进程，加载一次模型，对外提供两种入口。**与 pi 无关**，可独立运行、独立被调用。

| 入口 | 地址 | 用途 |
|---|---|---|
| unix socket | `~/.local/share/pi-asr/run/qwen-asr.sock` | 本机快路径（一行一个 JSON），pi 客户端用 |
| HTTP | `http://127.0.0.1:8178` | **OpenAI 兼容**，任何程序可用 |

## 文件

| 文件 | 说明 |
|---|---|
| `qwen_asr_server.py` | 服务端：加载模型（bf16→8bit→CPU 自动降级）+ unix socket + HTTP |
| `transcribe_qwen.py` | 客户端 CLI：优先 socket，连不上自动拉起服务，失败退进程内 |
| `compare_asr.py` | 同音频多模型 A/B 对比工具 |
| `whisper/transcribe.py` | faster-whisper 兜底转写（纯 CPU，秒级） |
| `systemd/qwen-asr.service` | systemd 用户服务单元 |
| `install.sh` | 一键安装/修复（幂等） |

## 安装

```bash
./install.sh                 # venv + 依赖 + 模型 + systemd + （可选）接进 pi-telegram
./install.sh --no-telegram   # 只装服务，不动 telegram.json
./install.sh --no-whisper    # 不装 whisper 兜底
```

装完：

```bash
systemctl --user status qwen-asr
curl -s http://127.0.0.1:8178/health
```

## HTTP API

| 方法 | 路径 | 参数 |
|---|---|---|
| POST | `/v1/audio/transcriptions` | multipart：`file`（必需）、`model`、`language`、`prompt`、`response_format=json\|text` |
| GET | `/v1/models` | — |
| GET | `/health` | — |

```bash
# JSON
curl -F "file=@a.ogg" -F "language=zh" http://127.0.0.1:8178/v1/audio/transcriptions
# {"text":"可以把 ZED 关掉，不要关 RasaDesk。","model":"qwen3-asr-1.7b","language":"zh","ms":2134}

# 纯文本
curl -F "file=@a.ogg" -F "response_format=text" http://127.0.0.1:8178/v1/audio/transcriptions

# Python（openai SDK / 任何支持自定义 base_url 的库或 App）
from openai import OpenAI
c = OpenAI(base_url="http://127.0.0.1:8178/v1", api_key="local")
print(c.audio.transcriptions.create(model="qwen3-asr-1.7b", file=open("a.ogg","rb")).text)
```

## 设备与显存策略

| 模式 | 权重 | 热调用 | 触发条件 |
|---|---|---|---|
| bf16 | 4.0GB | 0.4~0.7s | 空闲显存 ≥ `need + QWEN_ASR_HEADROOM`(默认1.2GB) |
| **8bit** | 2.4GB | 1.3~2.4s | 空闲 ≥ 3.6GB（8GB 卡常见） |
| CPU | 0 | ~16s | 显存不足 |

`/health` 的 `dev` / `quant` 字段即当前模式。

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `QWEN_ASR_MODEL` | `Qwen/Qwen3-ASR-1.7B-hf` | 模型仓库 |
| `QWEN_ASR_LANG` | `zh` | 语言（30 语言 + 22 中文方言） |
| `QWEN_ASR_PROMPT` | 内置热词表 | 领域词（ROI/竖直方向/开口左边线…） |
| `QWEN_ASR_DEVICE` | `auto` | `auto`/`cuda`/`cpu` |
| `QWEN_ASR_HEADROOM` | `1.2` | 服务必须留出的显存余量(GB)，设 0 可吃满 |
| `QWEN_ASR_HTTP` | 空(关) | HTTP 监听地址，如 `127.0.0.1:8178` |
| `QWEN_ASR_TOKEN` | 空 | HTTP Bearer token（对局域网暴露时必设） |
| `QWEN_ASR_IDLE` | `1800` | 空闲退出秒数（`0`=永不退出，systemd 下用） |
| `QWEN_ASR_SERVER` | `1` | 客户端是否用常驻服务（`0`=进程内） |
| `QWEN_ASR_SOCK` | `~/.local/share/pi-asr/run/qwen-asr.sock` | socket 路径 |

## 安全

默认只绑 `127.0.0.1`。要局域网使用：`--http 0.0.0.0:8178 --token <随机串>`，
客户端带 `Authorization: Bearer <随机串>`。**不要无 token 绑 `0.0.0.0`。**

## 运行期文件（不随仓库）

```
~/.local/share/pi-asr/
├── venv/                 # 依赖（torch/transformers/librosa/bitsandbytes）
├── run/qwen-asr.sock     # unix socket
├── run/qwen-asr.sock.log # 服务日志
└── samples/              # 本机测试样本
~/.cache/huggingface/hub/models--Qwen--Qwen3-ASR-1.7B-hf   # 权重（4.08GB）
```

排障见 [../skill/references/troubleshooting.md](../skill/references/troubleshooting.md)。
