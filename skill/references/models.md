# 模型选型与实测数据

## 本机当前配置

| 项 | 值 |
|---|---|
| 主模型 | **Qwen3-ASR-1.7B**（`Qwen/Qwen3-ASR-1.7B-hf`，4.08GB，Apache-2.0） |
| 兜底 | faster-whisper `small`（CPU，~3s/20s 音频） |
| 支持 | 30 语言 + 22 种中文方言、语言识别、中英混说 |
| 量化 | bf16(4.0GB) / 8bit(2.4GB，默认) / CPU，自动降级 |
| 接口 | unix socket（pi 内部）+ HTTP `127.0.0.1:8178`（OpenAI 兼容） |

## 2024-2026 中文 ASR 公开基准（CER，越低越好）

| 模型 | 中文 CER | 体积 | 8GB 显存 | 备注 |
|---|---|---|---|---|
| FireRedASR2S-LLM（小红书） | **2.89%** | 大 | ⚠️ 紧 | 最新旗舰 |
| **FireRedASR-AED-L** | **3.18%** | 1.1B | ✅ | Apache-2.0，纯中文最强 |
| SenseVoice-L | 4.47% | 234M | ✅ | 非自回归极快、多语种、情感标签 |
| Paraformer-large | 4.56% | 220M | ✅ | 支持**热词**、字级时间戳 |
| **Qwen3-ASR-1.7B** | 强（SOTA 开源之一） | 1.7B | ✅ | 52 语言/方言、时间戳、LLM 架构 |
| Whisper large-v3 / turbo | ~5-7% | 1.55B/809M | ✅ | 多语种稳，中文非强项 |
| Whisper small | ~10%+ | 244M | ✅ | 当前兜底 |
| **Qwen3-ASR-0.6B** | 更差 | 0.6B | ✅ | ❌ **实测把 ROI 听成 "L I"，不要用** |

## 本机实测（同音频对比，2026-09-19）

| 样本 | 模型 | 耗时 | 转写 |
|---|---|---|---|
| s01 (19.7s) | whisper-small CPU | 3.0s | 现在是不是说画一个**做开口**的ROI,然后以竖直方向为准,就能定位到**做开口**这条线。 |
| s01 | Qwen3-ASR-1.7B | 0.6~2.4s（服务热态） | 现在是不是画一个**左开口**的ROI，然后以竖直方向为准，就能定位到**左开口**这条线。 |
| s02 (5.6s) | whisper-small CPU | 2.7s | 可以把ZED关掉,不要关**RASATESC**。 |
| s02 | Qwen3-ASR-1.7B | 0.4~1.3s | 可以把 ZED 关掉，不要关 **Rasa Desk**。 |

**结论**：中英混说（ZED / RustDesk / ROI 这类工具名）Qwen 明显更稳，且自动加标点；
whisper 在纯中文虚词上偶尔更准。样本量小，结论方向性参考。

## 热词（重要）

Qwen3-ASR 支持 `prompt=` 传上下文，当前词表：

> ROI、竖直方向、水平方向、开口、开口左边线、拟合圆、圆心、外圆、内圆、六块、描边、夹角、工位、偏移、治具、产品、膜、焊线、像素、亚像素、Cobetter

加词方法：改 `~/.local/share/pi-asr/transcribe_qwen.py` 的 `DEFAULT_PROMPT`，或设环境变量 `QWEN_ASR_PROMPT`（systemd unit 的 `Environment=`），改完 `systemctl --user restart qwen-asr`。

## 换模型

```bash
# 例：换 0.6B（不推荐）或换成别的 HF 仓库
sudo -u "$USER" systemctl --user edit qwen-asr   # 加 Environment=QWEN_ASR_MODEL=...
systemctl --user restart qwen-asr
```

## Mac（Apple Silicon）路径

不要用 PyTorch MPS（bitsandbytes 支持差、要装 5.6GB torch）。用 **MLX**：

```bash
brew install uv ffmpeg && uv tool install mlx-audio
# 权重：mlx-community/Qwen3-ASR-1.7B-8bit（2.47GB）/ 4bit（1.61GB）
```

M4 16GB 预计热调用 1~2.5s（估算，未实测）；磁盘占用仅 ~3GB。
