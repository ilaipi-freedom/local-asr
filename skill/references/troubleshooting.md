# 排障手册

先跑 `scripts/doctor.sh`，多数问题它会直接指出来。

## 1. 发语音没有自动转写

| 检查 | 处理 |
|---|---|
| `telegram.json` 里有 `inboundHandlers` 吗 | `doctor.sh` 第 5 节会列顺序 |
| **改过配置后重启 pi 了吗** | ⚠️ 配置只在 pi 启动时读取，必须重启 |
| handler 路径是否有效 | doctor 会标 ❌ |
| 手工验证一次 | `scripts/transcribe.sh <音频> zh` |

## 2. 第一条语音很慢（~15s），后面很快

**正常，这是按需模式的设计**：服务默认不在跑（开机不自启、空闲 30 分钟自动退出），
第一条会按需拉起并加载 4GB 权重（~10–16s），之后热调用 1~3s。

想彻底避免冷启动 → 让服务常驻（代价：常占 ~4GB 显存）：

```bash
systemctl --user start qwen-asr                 # 本次开机内常驻（临时）
systemctl --user enable --now qwen-asr          # 开机也自启（配合 --idle 0 才不空闲退出）
```

## 2b. 服务没在跑 / 不知道有没有跑

```bash
scripts/service.sh status     # unit / socket / HTTP / 显存
scripts/service.sh ensure     # 没跑就拉起来并等就绪（幂等）
scripts/service.sh stop       # 停掉并释放显存
```

按需模式下 `is-active=inactive` 是**正常状态**，不是故障。若 `is-active=active` 但 `ping` 不通，
看 `journalctl --user -u qwen-asr -n 50`。

## 3. 热调用也很慢（~16s）

服务降级到 CPU 了。看 `/health` 的 `"dev"` 字段：

- `cpu` → 显存不足。`nvidia-smi` 看谁占了显存（Zed/RustDesk/浏览器/视频）。
- 8GB 卡 + 桌面 ≈ 3.5GB，服务需要 2.4GB（8bit），**必须留余量**。
- 调 `QWEN_ASR_HEADROOM`（默认 1.2GB）或关掉占显存的程序。

## 4. `CUDA out of memory` / `cuDevicePrimaryCtxRetain` 失败

显存被占满（常见于服务 + 进程内兜底同时要显存）。
**这是设计内行为**：客户端会退到 CPU，最坏情况落 whisper handler，不会丢消息。
彻底解决：`scripts/service.sh stop`（按需模式下默认就不在跑），或降低 `QWEN_ASR_HEADROOM`。

## 5. 服务起不来 / 起来就退

```bash
scripts/service.sh status
journalctl --user -u qwen-asr -n 80 --no-pager
tail -50 ~/.local/share/pi-asr/run/qwen-asr.sock.log
```

常见原因：模型没下完（`hf download Qwen/Qwen3-ASR-1.7B-hf`）、venv 缺依赖（`scripts/install.sh`）、
`socket 文件残留`（客户端会自动清理）、unit 被改坏（`systemctl --user cat qwen-asr` 对照 `scripts/install.sh` 里的模板）。

> 若 `is-active=active` 但 `systemctl is-enabled=disabled` —— 正常，按需模式就是手拉起来的。
> 若 `is-active=inactive` 但进程还在跑 —— 是客户端直接拉起的游离实例，`scripts/service.sh restart` 交给 systemd 接管。

## 6. 术语识别不准

加进热词表（见 references/models.md）。注意有些是**真歧义**（如"做/左"同音），热词也未必救得回来。

## 7. 别用 `pkill -f <脚本名>`

`pkill -f` 的模式会匹配到**你自己那条 shell 命令**（因为命令行里含这个字符串），
结果把自己杀掉、命令链静默中断。安全做法：

```bash
scripts/service.sh stop                 # 推荐（systemd + 游离进程都清，并释放显存）
systemctl --user stop qwen-asr          # 只管 systemd 拉起的实例
kill "$(cat ~/.local/share/pi-asr/run/server.pid)"   # 有 PID 文件时
pkill -f 'qwen_asr_ser''ver.py'         # 万不得已：把字符串拆开，避免自匹配
```

## 8. 端口 8178 被占 / 想改端口

```bash
ss -lntp | grep 8178
systemctl --user edit qwen-asr   # 改 ExecStart 的 --http 地址
systemctl --user daemon-reload   # 改 unit 后必须 reload
scripts/service.sh restart       # 下次调用也会自动带上新配置
```

## 9. 想让别的机器/手机用

必须同时开 token，且只在你信任的网络里：

```bash
systemctl --user edit qwen-asr   # ExecStart 加 --http 0.0.0.0:8178 --token <随机串>
systemctl --user daemon-reload
scripts/service.sh restart
curl -H "Authorization: Bearer <随机串>" -F "file=@a.ogg" http://<本机IP>:8178/v1/audio/transcriptions
```
