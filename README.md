# LocalASR

**本机离线语音识别服务 + pi skill。** 一个仓库，两个子目录，职责分离。

| 子目录 | 是什么 | 给谁用 |
|---|---|---|
| [`service/`](service/) | 服务：**Qwen3-ASR-1.7B**（主）+ **faster-whisper**（兜底）。同时提供 unix socket 与 OpenAI 兼容 HTTP。**按需启动**：开机不自启，空闲 30 分钟自动退出 | 任何程序：pi / curl / 脚本 / 第三方 App |
| [`skill/`](skill/) | pi skill：使用说明 + 自检/转写/服务启停/对比脚本（**不承载模型**） | pi agent（知识层） |

## 运行模式（按需，省显存）

服务**开机不自启**（`disabled`），空闲 **1800s** 自动退出并释放显存；第一次调用时自动拉起（冷启动 ~10–16s）。

```bash
~/.agents/skills/local-asr/scripts/service.sh ensure   # 没跑就拉起并等就绪（幂等，直接 curl 前先跑）
~/.agents/skills/local-asr/scripts/service.sh stop     # 停掉并释放显存
~/.agents/skills/local-asr/scripts/service.sh status   # unit / socket / HTTP / 显存
```

转写入口（`transcribe.sh` / telegram handler）已内置自动拉起；想恢复常驻见 [service/README.md](service/README.md)。

## 架构（三层，别混职责）

```
能力层   systemd --user 服务 qwen-asr            ← 独立进程，与 pi 无关，按需启停
         service/qwen_asr_server.py
           ├─ unix socket  ~/.local/share/pi-asr/run/qwen-asr.sock   ← pi 内部快路径（热 1~3s）
           └─ HTTP         http://127.0.0.1:8178                     ← OpenAI 兼容，任何程序
自动化层 pi-telegram handler（telegram.json 的 inboundHandlers）
           发语音 → 自动转文字进 prompt（服务没跑会自动拉起；失败落 whisper，不丢消息）
知识层   skill/local-asr → 安装到 ~/.agents/skills/local-asr
           自检 / 转写 / 服务启停 / A-B 对比 / 排障手册
```

**关键约定**：skill 只做"说明书 + 工具箱"，通过**接口**调用服务，不内嵌模型、不负责自动转写。

## 快速开始（本机部署/更新）

```bash
./sync.sh              # 把 service/ 部署到 ~/.local/share/pi-asr，skill/ 部署到 ~/.agents/skills/local-asr
./sync.sh --link       # 同上，但 skill 用软链（改仓库即时生效，便于开发）

~/.agents/skills/local-asr/scripts/doctor.sh     # 自检
~/.agents/skills/local-asr/scripts/transcribe.sh a.ogg zh   # 转写一个文件
```

换新机器：`service/install.sh`（装运行时 + systemd）或直接 `skill/scripts/install.sh`（skill 自带运行时副本，自包含）。
两者默认都**不开机自启**（按需模式）；要开机常驻加 `--autostart` 并把 unit 的 `--idle 1800` 改成 `--idle 0`。

## 接口速查

```bash
# HTTP（OpenAI 兼容）
curl -F "file=@a.ogg" -F "language=zh" http://127.0.0.1:8178/v1/audio/transcriptions
curl -s http://127.0.0.1:8178/health          # {"ok":true,"dev":"cuda","quant":"8bit",...}

# 服务管理（按需模式）
~/.agents/skills/local-asr/scripts/service.sh ensure|stop|status
systemctl --user status|start|stop qwen-asr
journalctl --user -u qwen-asr -n 50
```

## 目录

```
LocalASR/
├── service/          # 运行时（服务端 + 客户端 + 对比工具 + systemd + whisper 兜底）
├── skill/            # pi skill（SKILL.md + scripts + references + assets）
├── samples/          # 测试语音样本与 A/B 结果
└── sync.sh           # 部署脚本（仓库 → 系统位置）
```

## 实测数据（2026-09-19，RTX 3060 Ti 8GB）

| 场景 | 耗时 |
|---|---|
| 冷启动（按需拉起，加载模型） | ~10–16s |
| 热调用（19.7s 音频，8bit） | 1.3~2.4s |
| 热调用（bf16） | 0.4~0.7s |
| 兜底 whisper-small（CPU） | ~3s |
| 空闲退出 | 1800s 无请求后自动退出，显存归零 |

模型选型与精度对比见 [skill/references/models.md](skill/references/models.md)，
排障见 [skill/references/troubleshooting.md](skill/references/troubleshooting.md)。

## 许可与第三方

- 本项目（脚本/文档/skill）：**MIT**，见 [LICENSE](LICENSE)
- 依赖的模型与库各自许可：
  - [Qwen3-ASR](https://huggingface.co/Qwen/Qwen3-ASR-1.7B-hf) — Apache-2.0
  - [faster-whisper](https://github.com/SYSTRAN/faster-whisper) / [Whisper](https://github.com/openai/whisper) — MIT
  - PyTorch / Transformers — BSD-3 / Apache-2.0
- 模型权重**不随仓库分发**（首次运行自动下载到 `~/.cache/huggingface`）

## 远程

计划发布到 GitHub（个人项目）：`git@github.com:<account>/LocalASR.git`
