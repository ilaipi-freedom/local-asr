# skill —— pi skill（local-asr）

给 pi agent 用的**知识层**：说明本机有本地 ASR 服务、怎么用、怎么修、怎么调、怎么对比。
**不承载模型、不负责自动转写**——它只是"说明书 + 工具箱"，通过接口调用 `../service/`。

## 安装位置与方式

| 方式 | 命令 | 说明 |
|---|---|---|
| 仓库部署（推荐） | `../sync.sh` | 复制到 `~/.agents/skills/local-asr/` |
| 开发模式 | `../sync.sh --link` | 软链到仓库，改完即时生效 |
| 新机器 | `scripts/install.sh` | 用自带 `assets/` 装服务 + 写 systemd + 可选接 telegram |

pi 在**会话启动时**发现 skill —— 装完要新开一个会话才会加载。

## 结构

```
skill/
├── SKILL.md              # frontmatter + 拓扑 + 入口选择 + 常用操作 + 常见任务 + 坑
├── scripts/
│   ├── install.sh        # 一键安装/修复（优先用 ../service/，否则用 assets/）
│   ├── doctor.sh         # 自检：服务/显存/量化/模型/配置/handler 顺序
│   ├── transcribe.sh     # 转写（自动选 HTTP → socket → 进程内 → whisper）
│   └── bench.sh          # 用 ../samples/ 跑模型 A/B
├── references/
│   ├── models.md         # 选型 + 本机实测 + 热词 + Mac(MLX) 路径
│   └── troubleshooting.md# 9 类故障处理
└── assets/pi-asr/        # 运行时脚本副本（由 ../sync.sh 从 service/ 回填，保证自包含）
```

## 何时会被加载

SKILL.md 的 description 覆盖：语音转文字 / 转写 / 听写 / 语音识别 / whisper / Qwen3-ASR /
ASR 服务排障（服务没起、首次慢、设备或量化不对、显存不足、模型缺失、handler 顺序）。

## 常用命令

```bash
scripts/doctor.sh                     # 先自检
scripts/transcribe.sh a.ogg zh        # 转写
scripts/transcribe.sh a.ogg zh --whisper   # 强制走兜底
scripts/bench.sh                      # 模型 A/B
```

## 与 service 的边界

| | service | skill |
|---|---|---|
| 提供能力 | ✅ 模型常驻、socket/HTTP 接口 | ❌ |
| 自动转写 Telegram 语音 | ❌（由 telegram.json 的 handler 负责） | ❌ |
| 知道怎么装/修/调/比 | ❌ | ✅ |
| 独立于 pi 运行 | ✅ | ❌（skill 只在 pi 会话里生效） |
