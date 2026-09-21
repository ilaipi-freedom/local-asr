---
name: local-asr
description: "Local speech-to-text on this machine (Telegram voice, dictation, subtitles, batch transcription). Use when the user mentions voice/audio transcription, whisper, Qwen3-ASR, ASR service, 语音转文字, 转写, 听写, 语音识别, or when diagnosing that service (not running, on-demand start, slow first call, wrong device/quantization, VRAM pressure, missing model, handler order). The service does NOT autostart at boot and auto-exits when idle — run scripts/service.sh ensure before any direct call. Documents the service topology, its unix-socket and OpenAI-compatible HTTP APIs, plus self-check/repair/benchmark scripts, model-selection data, and known pitfalls."
compatibility: "Linux + systemd --user. Python 3.12 venv at ~/.local/share/pi-asr. NVIDIA GPU optional (auto bf16 → 8bit → CPU). Whisper fallback at ~/.local/share/pi-whisper."
---

# Local ASR（本机语音识别服务）

本机有一个**独立于 pi** 的语音转文字服务：主模型 **Qwen3-ASR-1.7B**（本地、离线），
兜底 **faster-whisper-small**。由 systemd 用户服务 `qwen-asr` 管理。

## ⚠️ 运行模式：按需启动（2026-09-21 起）

**开机不自启，空闲自动退出**——目的是别让 4GB 显存常驻（系统扛不住）。

| 行为 | 值 |
|---|---|
| 开机自启 | ❌ `disabled`（不要 enable，除非用户明确要常驻） |
| 冷启动（首次调用自动拉起） | ~10–16s（加载权重） |
| 热调用 | 1.0–2.5s（8bit）/ 0.4–0.7s（bf16） |
| 空闲退出 | 1800s（30 分钟）后自动退出并释放显存 |
| 谁负责拉起 | `transcribe.sh` / telegram handler 内置；手工 HTTP 调用前先 `service.sh ensure` |

## 调用前先确保服务在跑（最重要）

```bash
scripts/service.sh ensure     # 没跑就按需拉起并等就绪；已在跑则秒回（幂等）→ 直接 curl 前调它
scripts/service.sh status     # unit / socket / HTTP / 显存 一眼看
scripts/service.sh stop       # 停掉并释放显存（systemd 实例 + 游离进程都清）
scripts/service.sh restart    # 改配置后用（会重新加载模型）
```

> `scripts/transcribe.sh` 和 telegram 语音 handler **已经内置自动拉起**（走 `systemctl --user start qwen-asr`），
> 所以转写本身不需要先 ensure。**只有**直接 `curl`/自写脚本/别的 App 调用时才要先 ensure。

## 拓扑（三层，分清职责）

```
能力层   systemd --user 服务 qwen-asr        ← 按需启停，与 pi 无关
         ~/.local/share/pi-asr/qwen_asr_server.py
           ├─ unix socket  ~/.local/share/pi-asr/run/qwen-asr.sock   ← pi 内部快路径
           └─ HTTP         http://127.0.0.1:8178                     ← 任何程序
自动化层 pi-telegram handler（telegram.json 的 inboundHandlers）
           发语音 → 自动转文字进 prompt（服务没跑会自动拉起）
知识层   本 skill = 使用说明书 + 工具箱（不承载模型，不负责自动转写）
```

## 三个入口，怎么选

| 入口 | 命令 | 何时用 |
|---|---|---|
| unix socket | `scripts/transcribe.sh <audio>` | 默认；最快（热 1~3s），服务没跑自动拉起 |
| HTTP | `scripts/service.sh ensure && curl -F "file=@a.ogg" http://127.0.0.1:8178/v1/audio/transcriptions` | 其它程序/脚本/第三方 App（**必须先 ensure**） |
| 进程内 | `scripts/transcribe.sh --inproc <audio>` | 调试，或不想启服务 |

## 常用操作

```bash
scripts/service.sh ensure              # 按需拉起（推荐入口）
scripts/service.sh status              # 状态 + 显存
systemctl --user status  qwen-asr      # 状态（active/8bit/cuda 等）
systemctl --user start   qwen-asr      # 只启动，不等就绪
systemctl --user stop    qwen-asr      # 停 → 立即释放显存
journalctl --user -u qwen-asr -n 50    # 服务日志
curl -s http://127.0.0.1:8178/health   # {"ok":true,"dev":"cuda","quant":"8bit",...}
```

**先自检**：`scripts/doctor.sh` —— 一次查出服务/显存/量化/模型/配置/handler 顺序的问题。

## 常见任务

| 任务 | 做法 |
|---|---|
| 转写一个音频 | `scripts/transcribe.sh <audio> [zh]` |
| 其它程序调用 | 先 `scripts/service.sh ensure`，再 `curl -F "file=@a.ogg" http://127.0.0.1:8178/v1/audio/transcriptions` |
| 术语识别不准 | 往热词表加词：改 `~/.local/share/pi-asr/transcribe_qwen.py` 的 `DEFAULT_PROMPT`，或设 `QWEN_ASR_PROMPT`；改完 `scripts/service.sh restart` |
| 换模型 | `QWEN_ASR_MODEL=Qwen/Qwen3-ASR-0.6B-hf`（更快更差）→ 写进 unit 的 `Environment=` 后 `scripts/service.sh restart` |
| 调显存占用 | unit 里 `QWEN_ASR_HEADROOM`（默认 1.2GB）；显存紧张会自动降到 8bit（2.4GB） |
| 调空闲退出时间 | unit 的 `--idle <秒>`（默认 1800；`0`=永不退出）→ `daemon-reload` |
| 临时常驻（用完再说） | `systemctl --user start qwen-asr`（本次开机内一直跑） |
| **恢复开机常驻**（用户明确要求时才做） | `systemctl --user edit qwen-asr` 把 `--idle 1800` 改成 `--idle 0` → `systemctl --user enable --now qwen-asr` |
| 模型 A/B 对比 | `scripts/bench.sh`（用 `~/.local/share/pi-asr/samples/` 里的样本） |
| 装到新机器 | `scripts/install.sh`（幂等：venv + 依赖 + 模型 + systemd unit + 可选写 telegram.json；默认**不开机自启**，加 `--autostart` 才自启） |

## 关键坑（详见 references/troubleshooting.md）

1. **服务默认不在跑**（这是设计，不是故障）。要调用先 `service.sh ensure`；别报"服务挂了"。
2. **telegram.json 改完必须重启 pi** 才生效（配置在启动时读取）。
3. **显存必须留余量**：8GB 卡上桌面常占 3.5GB；服务默认留 1.2GB，否则兜底路径会 OOM。
4. **量化自动降级**：bf16(4.0GB) → 8bit(2.4GB) → CPU(~16s)。`/health` 里能看到当前模式。
5. **0.6B 不要用**：实测把 "ROI" 听成 "L I"。
6. **别用 `pkill -f <脚本名>`**：模式会匹配到自己的 shell 命令行，把自己杀掉；用 `service.sh stop` / `systemctl` / PID 文件。
7. handler 顺序 = 优先级：`qwen3-asr-1.7b` 在前，`local-whisper-fallback` 在后（失败自动落兜底，不丢消息）。
8. 冷启动 10~16s 属于正常：**第一条语音慢**是加载权重，不是卡死（超时上限 150s / handler 300s）。

## 参考

- [references/models.md](references/models.md) —— 模型选型与实测数据
- [references/troubleshooting.md](references/troubleshooting.md) —— 排障手册
- 主文档：`~/.local/share/pi-asr/README.md`
