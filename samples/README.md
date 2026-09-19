# samples —— 语音测试样本

用于模型 A/B 对比与回归验证。

| 文件 | 内容 | 时长 |
|---|---|---|
| `s01.ogg` | "现在是不是画一个做/左开口的 ROI，然后以竖直方向为准，就能定位到这条线" | 19.7s |
| `s02.ogg` | "可以把 ZED 关掉，不要关 RustDesk" | 5.6s |
| `results.md` | 逐样本对比记录（whisper-small vs Qwen3-ASR-1.7B） | — |

## 跑对比

```bash
../skill/scripts/bench.sh            # 遍历本目录 *.ogg
# 或
QWEN_MODELS="Qwen/Qwen3-ASR-1.7B-hf" \
  ~/.local/share/pi-asr/venv/bin/python ~/.local/share/pi-asr/compare_asr.py s01.ogg zh
```

## 加样本

把新语音放进本目录（`.ogg`/`.wav` 皆可），在 `results.md` 里追加一行结论。
有 ground truth（人工转写）时写进 `results.md`，就能算字错率而不是靠肉眼判断。
