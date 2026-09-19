# 语音样本 A/B 记录（whisper-small CPU vs Qwen3-ASR-1.7B GPU）

| 样本 | 时长 | 模型 | 耗时 | 转写 |
|---|---|---|---|---|
| s01 | 19.7s | whisper-small | 3.0s | 现在是不是说画一个**做开口**的ROI,然后以竖直方向为准,就能定位到**做开口**这条线。 |
| s01 | 19.7s | Qwen3-ASR-1.7B | 13.9s | 现在是不是画一个**左开口**的ROI，然后以竖直方向为准，就能定位到**左开口**这条线。 |
| s02 | 5.6s | whisper-small | 2.7s | 可以把ZED关掉,不要关**RASATESC**。 |
| s02 | 5.6s | Qwen3-ASR-1.7B | 14.1s | 可以把 ZED 关掉，不要关 **Rasa Desk**。 |

## 观察

- **s01**：whisper 对了虚词"做"；Qwen 对了 ROI 且**自动加标点**（对 prompt 更友好）。基本平手。
- **s02**：**Qwen 明显更好**。whisper 把 RustDesk 变成无意义大写串 "RASATESC"；Qwen 给出 "Rasa Desk"（保留了英文词的音形）。
- **中英混说场景 Qwen 优势明显**（ZED / RustDesk 这类工具名）。
- **耗时**：Qwen 14s 中约 11s 是**模型加载**（每条语音都重新加载）→ 常驻服务可降到 1~3s。

## 待补

- 样本量 2，需要 5~10 条，最好含术语（竖直方向/开口左边线/拟合圆/工位2/六块描边）。
- 若有真实答案（ground truth），可算字错率，避免肉眼判断。
