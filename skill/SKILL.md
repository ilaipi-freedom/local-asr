---
name: local-asr
description: "Local speech-to-text on this machine (Telegram voice, dictation, subtitles, batch transcription). Use when the user mentions voice/audio transcription, whisper, Qwen3-ASR, ASR service, 语音转文字, 转写, 听写, 语音识别, or when diagnosing that service (not running, slow first call, wrong device/quantization, VRAM pressure, missing model, handler order). Documents the service topology, its unix-socket and OpenAI-compatible HTTP APIs, plus self-check/repair/benchmark scripts, model-selection data, and known pitfalls."
compatibility: "Linux + systemd --user. Python 3.12 venv at ~/.local/share/pi-asr. NVIDIA GPU optional (auto bf16 → 8bit → CPU). Whisper fallback at ~/.local/share/pi-whisper."
---

# Local ASR（本机语音识别服务）

本机有一个**独立于 pi** 的语音转文字服务：主模型 **Qwen3-ASR-1.7B**（本地、离线），
兜底 **faster-whisper-small**。服务由 systemd 用户服务 `qwen-asr` 管理。

## 拓扑（三层，分清职责）

```
能力层   systemd --user 服务 qwen-asr        ← 独立进程，与 pi 无关
         ~/.local/share/pi-asr/qwen_asr_server.py
           ├─ unix socket  ~/.local/share/pi-asr/run/qwen-asr.sock   ← pi 内部快路径
           └─ HTTP         http://127.0.0.1:8178                     ← 任何程序
自动化层 pi-telegram handler（telegram.json 的 inboundHandlers）
           发语音 → 自动转文字进 prompt（无需人工）
知识层   本 skill = 使用说明书 + 工具箱（不承载模型，不负责自动转写）
```

## 三个入口，怎么选

| 入口 | 命令 | 何时用 |
|---|---|---|
| unix socket | `scripts/transcribe.sh <audio>` | 默认；最快（1~3s），自动拉起服务 |
| HTTP | `curl -F "file=@a.ogg" http://127.0.0.1:8178/v1/audio/transcriptions` | 其它程序/脚本/第三方 App |
| 进程内 | `scripts/transcribe.sh --inproc <audio>` | 调试，或不想常驻 |

## 常用操作

```bash
systemctl --user status  qwen-asr      # 状态（active/8bit/cuda 等）
systemctl --user restart qwen-asr      # 重启（换模型/改配置后）
systemctl --user stop    qwen-asr      # 停 → 释放显存
journalctl --user -u qwen-asr -n 50    # 服务日志
curl -s http://127.0.0.1:8178/health   # {"ok":true,"dev":"cuda","quant":"8bit",...}
```

**先自检**：`scripts/doctor.sh` —— 一次查出服务/显存/量化/模型/配置/handler 顺序的问题。

## 常见任务

| 任务 | 做法 |
|---|---|
| 转写一个音频 | `scripts/transcribe.sh <audio> [zh]` |
| 术语识别不准 | 往热词表加词：改 `~/.local/share/pi-asr/transcribe_qwen.py` 的 `DEFAULT_PROMPT`，或设 `QWEN_ASR_PROMPT`；改完 `systemctl --user restart qwen-asr` |
| 换模型 | `QWEN_ASR_MODEL=Qwen/Qwen3-ASR-0.6B-hf`（更快更差）→ 写进 unit 的 `Environment=` 后 restart |
| 调显存占用 | unit 里 `QWEN_ASR_HEADROOM`（默认 1.2GB）；显存紧张会自动降到 8bit（2.4GB） |
| 模型 A/B 对比 | `scripts/bench.sh`（用 `~/.local/share/pi-asr/samples/` 里的样本） |
| 别的程序调用 | 用 HTTP（OpenAI 兼容），见 `references/models.md` 与主 README |
| 装到新机器 | `scripts/install.sh`（幂等：venv + 依赖 + 模型 + systemd unit + 可选写 telegram.json） |

## 关键坑（详见 references/troubleshooting.md）

1. **telegram.json 改完必须重启 pi** 才生效（配置在启动时读取）。
2. **显存必须留余量**：8GB 卡上桌面常占 3.5GB；服务默认留 1.2GB，否则兜底路径会 OOM。
3. **量化自动降级**：bf16(4.0GB) → 8bit(2.4GB) → CPU(~16s)。`/health` 里能看到当前模式。
4. **0.6B 不要用**：实测把 "ROI" 听成 "L I"。
5. **别用 `pkill -f <脚本名>`**：模式会匹配到自己的 shell 命令行，把自己杀掉；用 `systemctl` 或 PID 文件。
6. handler 顺序 = 优先级：`qwen3-asr-1.7b` 在前，`local-whisper-fallback` 在后（失败自动落兜底，不丢消息）。

## 参考

- [references/models.md](references/models.md) —— 模型选型与实测数据
- [references/troubleshooting.md](references/troubleshooting.md) —— 排障手册
- 主文档：`~/.local/share/pi-asr/README.md`
