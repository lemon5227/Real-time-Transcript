# Nemotron CoreML 说话人识别接入设计

## 目标

在不改变 Parakeet MLX 实时转录链路的前提下，为 macOS Apple Silicon 增加可选的本地说话人识别，让实时字幕可以异步显示“老师 / 同学”等说话人信息，并在课后复习阶段使用原声重新校正说话人时间轴。

## 非目标

- 不替换 Parakeet MLX，不把说话人识别混入 ASR 推理线程。
- 不做声源分离、降噪或“自动知道谁是老师”；模型只输出匿名说话人通道。
- 不在本轮实现 Windows/CUDA 或云端说话人识别。
- 不要求没有本地 Nemotron helper 的安装包阻塞课堂转录。

## 方案

采用“音频扇出 + 独立 native helper + 时间轴合并”的结构：

```text
浏览器 PCM
    │
    ├── 原声录音（立即写入 IndexedDB）
    ├── Parakeet MLX worker ──> transcript_segment
    └── diarization queue
             │
             ▼
      Swift/CoreML helper
      Nemotron 3 Diarization
             │
             ▼
      speaker turns
             │
             ▼
      SpeakerTimeline / attribution
             │
      transcript_segment_updated
```

Python 后端负责会话生命周期、队列、时间戳和事件；Swift helper 只负责加载 FluidAudio/Nemotron CoreML、接收 16kHz PCM 和返回说话人 turn。通信采用 stdin/stdout JSON Lines，便于本地测试、DMG 打包和未来替换为 CUDA helper。helper 崩溃、缺失或模型未下载时，Python 自动退化为无说话人标签的正常转录。

## 数据契约

`TranscriptSegment` 保持已有字段，并追加可选字段：

```json
{
  "id": "segment-123",
  "text": "The model is trained on...",
  "start_ms": 12000,
  "end_ms": 16800,
  "is_final": true,
  "confidence": 0.91,
  "speaker_id": "speaker_0",
  "speaker_confidence": 0.88
}
```

说话人识别晚于字幕时，先发送原有字幕，再发送同一 `id` 的 `transcript_segment_updated`；前端按 id 原地更新，不追加重复句。说话人置信度低于阈值时保留空值，避免错误标签干扰学习。

helper 输入输出约定：

```json
{"type":"start","sample_rate":16000,"variant":"low"}
{"type":"audio","start_ms":12000,"pcm16_base64":"..."}
{"type":"flush"}
{"type":"stop"}
```

```json
{"type":"ready","variant":"low","model":"Nemotron-3-Diarization"}
{"type":"speaker_turns","turns":[{"speaker_id":"speaker_0","start_ms":12000,"end_ms":14800,"confidence":0.89}]}
{"type":"error","code":"DIARIZATION_MODEL_UNAVAILABLE","message":"..."}
```

## 生命周期与性能

1. 点击开始听课后，原声录音和 Parakeet 立即启动；Nemotron helper 异步预热。
2. Python 将已重采样的 16kHz 音频复制到 ASR 队列和 diarization 队列，两个 worker 互不阻塞。
3. 实时使用低延迟 CoreML variant；课后精细复习使用更高质量 variant，重新读取原声。
4. stop 时先 flush ASR，再 flush diarizer，等待有限时间；超时不影响已保存的录音和字幕。
5. 模型缓存继续复用 Hugging Face/FluidAudio 共享缓存，不复制用户已有权重。

## 错误处理

- helper 命令未找到：显示“说话人识别不可用”，字幕继续工作。
- CoreML/ANE 加载失败：记录完整日志，关闭该会话的 diarization，保留 ASR。
- JSONL 输出损坏或进程退出：一次性断开该 helper，禁止重启风暴。
- audio queue 满：遵循现有 ASR 背压策略，记录丢弃量；绝不丢弃已保存原声。
- 说话人 turn 与字幕只按时间重叠合并；无足够重叠时不猜测。

## UI 行为

- 主页面不改变现有字幕布局；说话人标签以低存在感的小字/色点显示在字幕元信息行。
- 设置页增加“说话人识别”开关、模型状态、当前 helper 状态和“仅课后识别”选项。
- 复习页支持按说话人筛选，但匿名标签显示为“老师 / 同学 1 / 同学 2”，允许用户手动改名。
- 说话人识别不可用时隐藏空标签，不显示错误卡片遮挡课堂内容。

## 测试策略

- Python 单测覆盖 turn 合并、时间重叠归属、晚到更新、helper 不可用降级和会话停止。
- JSONL helper 客户端使用 fake subprocess 做协议测试，不依赖 CoreML/Apple Neural Engine。
- macOS 专项测试在有 Swift/FluidAudio 的机器上验证 helper 启动、16k 音频输入、two-speaker fixture 和异常退出。
- 端到端验收：同一段录音同时检查原声、实时字幕、说话人更新事件、复习页精细稿。

## 采用理由

这个方案保留了当前已经验证过的实时 ASR 稳定性，把新增能力隔离在可替换边界之后；它比在 Python 内嵌 CoreML 更适合 macOS，也比等待说话人识别完成后再显示字幕更符合课堂实时场景。未来 Windows/CUDA 只需实现相同的 diarizer helper/adapter 契约，不需要重做前端和复习数据模型。
