# pi-asr（Qwen3-ASR 本地语音转写）

给 pi-telegram 用的**离线**语音转文字，主处理器为 **Qwen3-ASR-1.7B**（阿里，Apache-2.0）。
与 `~/.local/share/pi-whisper`（faster-whisper，作为兜底）配合使用。

## 结构

```
~/.local/share/pi-asr/
├── transcribe_qwen.py   # 入口脚本（stdout 输出纯文本；计时/日志走 stderr）
└── venv/                # torch 2.14+cu130 / transformers 5.17 / librosa / bitsandbytes（5.6GB）
```

模型缓存：`~/.cache/huggingface/hub/models--Qwen--Qwen3-ASR-1.7B-hf`（4.08GB）

## 设备策略（自动三级降级）

| 顺序 | 条件 | 表现 |
|---|---|---|
| ① bf16 @ GPU | 空闲显存 ≥ 3.8GB | 加载 ~2s，推理 ~1s |
| ② **8-bit @ GPU** | 空闲显存 ≥ 2.4GB | 加载 ~11s，推理 ~3s（显存 2.36GB） |
| ③ CPU | 其它情况 | 加载 ~2s，推理 ~18s（20 线程） |

显存不够会自动往下降，不会失败。想让它走 ① 就**关掉占显存的桌面程序**（Zed ~292MB、RustDesk ~729MB）。

## 接线（已配置）

`~/.pi/agent/telegram.json` 顶层 `inboundHandlers`，**顺序即优先级**：

```json
[
  {"label":"qwen3-asr-1.7b","type":"voice",     "template":["…/pi-asr/venv/bin/python","…/transcribe_qwen.py","--file","{file}","--lang","{lang=zh}"],"timeout":300000},
  {"label":"local-whisper-fallback","type":"voice","template":["…/pi-whisper/venv/bin/python","…/transcribe.py","--file","{file}","--lang","{lang=zh}"],"timeout":300000},
  … audio/* 同上两条 …
]
```

Qwen 失败（OOM/依赖缺失/空结果）时自动落到 whisper，不会丢消息。
**配置在 pi 启动时读取 → 改完要重启 pi 会话。**

## 热词（领域词表）

Qwen3-ASR 支持 `prompt=` 传上下文，默认词表在脚本 `DEFAULT_PROMPT`：

> ROI、竖直方向、水平方向、开口、开口左边线、拟合圆、圆心、外圆、内圆、六块、描边、夹角、工位、偏移、治具、产品、膜、焊线、像素、亚像素、Cobetter

术语识别不准就往里加词（改 `DEFAULT_PROMPT` 或设 `QWEN_ASR_PROMPT`）。

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `QWEN_ASR_MODEL` | `Qwen/Qwen3-ASR-1.7B-hf` | 也可用 `…-0.6B-hf`（更快但更差，实测会把 ROI 听成 "L I"） |
| `QWEN_ASR_LANG` | `zh` | 支持 30 语言 + 22 中文方言 |
| `QWEN_ASR_PROMPT` | 内置词表 | 热词 |
| `QWEN_ASR_DEVICE` | `auto` | `auto`/`cuda`/`cpu` |
| `QWEN_ASR_MAXTOK` | `256` | 生成上限 |

## 对比工具

```bash
# 同一段音频上跑 whisper + Qwen(可多个) 并排对比
~/.local/share/pi-asr/venv/bin/python ~/.local/share/pi-asr/compare_asr.py <音频> zh
# 只比 1.7B：
QWEN_MODELS="Qwen/Qwen3-ASR-1.7B-hf" … compare_asr.py <音频> zh
```

## 已知结论（单样本，2026-09-19）

| 模型 | 耗时 | 结果 |
|---|---|---|
| whisper-small CPU | 3.1s | 对"做开口"，无标点 |
| **Qwen3-ASR-1.7B** | 3.2s(GPU/8bit) / 18s(CPU) | 对"ROI"+自动标点，错"左开口" |
| Qwen3-ASR-0.6B | 0.35s(GPU) | 把 ROI 听成 "L I"，**不用** |

样本量 1，结论仅供方向参考；需要 5~10 条语音做正式对比。

## 常驻服务（v2，2026-09-19 晚）

模型只加载一次，之后每条语音 **1~3s**（原来是 14s：每条都要重读 4GB 权重）。

```
transcribe_qwen.py（客户端）
   ├─ 先连 unix socket: ~/.local/share/pi-asr/run/qwen-asr.sock
   ├─ 连不上 → 自动拉起 qwen_asr_server.py（flock 防并发重复拉起），等就绪
   ├─ 服务不可用/返回错误 → 退化为"进程内加载"
   └─ 仍失败 → 非 0 退出，bridge 落到 whisper handler（不丢消息）
```

### 实测

| 场景 | 耗时 |
|---|---|
| 冷启动（拉起服务 + 加载模型，8bit） | 16.2s |
| 热调用（19.7s 音频） | **2.4s** |
| 热调用（5.6s 音频） | **1.3s** |
| 热调用（bf16 模式） | **0.4~0.7s** |
| 兜底（服务占着显存 → CPU） | 16.5s |

### 显存策略（常驻服务要留余量）

| 模式 | 权重 | 额外留出 | 热调用 |
|---|---|---|---|
| bf16 | 4.0GB | `QWEN_ASR_HEADROOM`（默认 1.2GB） | 0.4~0.7s |
| **8bit（默认，显存紧时自动选）** | 2.4GB | 同上 | 1.3~2.4s |
| CPU | 0 | — | ~16s |

服务空闲 `QWEN_ASR_IDLE`（默认 1800s=30 分钟）自动退出并释放显存。

### 环境变量（新增）

| 变量 | 默认 | 说明 |
|---|---|---|
| `QWEN_ASR_SERVER` | `1` | `0` 关闭常驻服务，走进程内加载 |
| `QWEN_ASR_SOCK` | `~/.local/share/pi-asr/run/qwen-asr.sock` | socket 路径 |
| `QWEN_ASR_STARTUP` | `150` | 首次拉起服务的等待上限（秒） |
| `QWEN_ASR_IDLE` | `1800` | 服务空闲退出（秒） |
| `QWEN_ASR_HEADROOM` | `1.2` | 服务必须留出的显存余量（GB）；设 0 允许吃满 |

手动管理：
```bash
pgrep -af qwen_asr_server.py            # 看是否在跑
pkill -f 'qwen_asr_server[.]py'         # 停掉（会释放显存）
tail -f ~/.local/share/pi-asr/run/qwen-asr.sock.log   # 看服务日志
```

## 对外接口（v3，2026-09-19 晚）

一个进程、一个模型、**两个入口**：

| 入口 | 给谁用 | 地址 |
|---|---|---|
| **unix socket** | pi-telegram 的 handler（内部快路径，最快最安全） | `~/.local/share/pi-asr/run/qwen-asr.sock` |
| **HTTP（OpenAI 兼容）** | 任何其它程序：curl / 脚本 / 第三方 App / 手机 | `http://127.0.0.1:8178` |

### HTTP API

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/v1/audio/transcriptions` | multipart/form-data：`file`（必需）、`model`、`language`、`prompt`、`response_format=json\|text` |
| GET | `/v1/models` | 模型列表（OpenAI 格式） |
| GET | `/health` | 状态：设备/量化/模型名 |

```bash
# curl
curl -F "file=@a.ogg" -F "language=zh" http://127.0.0.1:8178/v1/audio/transcriptions
# {"text": "可以把 ZED 关掉，不要关 RasaDesk。", "model": "qwen3-asr-1.7b", "ms": 2134}

# 纯文本
curl -F "file=@a.ogg" -F "response_format=text" http://127.0.0.1:8178/v1/audio/transcriptions

# Python（openai SDK，任何支持自定义 base_url 的库/App 同理）
from openai import OpenAI
c = OpenAI(base_url="http://127.0.0.1:8178/v1", api_key="local")
print(c.audio.transcriptions.create(model="qwen3-asr-1.7b", file=open("a.ogg","rb")).text)
```

### 系统服务（开机常驻，供其它程序随时调用）

已创建 `~/.config/systemd/user/qwen-asr.service`（unix socket + HTTP，`--idle 0` 不自动退出）：

```bash
systemctl --user status  qwen-asr     # 查看
systemctl --user restart qwen-asr     # 重启
systemctl --user stop    qwen-asr     # 停（释放显存）
systemctl --user enable --now qwen-asr   # 开机自启（需要常驻时）
loginctl enable-linger $USER             # 未登录也运行（可选）
journalctl --user -u qwen-asr -f         # 日志
```

不想常驻：`systemctl --user disable --now qwen-asr`，改回"按需拉起"（客户端会自动启动，空闲 30 分钟退出）。

### 安全

- 默认只绑 `127.0.0.1`，本机可用；
- 要局域网访问：`--http 0.0.0.0:8178 --token <随机串>`，客户端加 `Authorization: Bearer <token>`；
- **不要**在无 token 情况下绑 `0.0.0.0`。

### 三种调用方式对照

| 方式 | 命令 | 适合 |
|---|---|---|
| pi 内部 | `transcribe_qwen.py --file a.ogg --lang zh` | Telegram 语音（走 socket，1~3s） |
| HTTP | `curl -F file=@a.ogg http://127.0.0.1:8178/v1/audio/transcriptions` | 其它程序/脚本/App |
| 进程内 | `transcribe_qwen.py --no-server ...` | 调试、或不想常驻 |
