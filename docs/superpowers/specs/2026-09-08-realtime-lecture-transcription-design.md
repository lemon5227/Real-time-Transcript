# 留学生听课实时转录工作台设计规格

**日期：** 2026-09-08  
**状态：** 核心实现已落地；设备级课堂验收状态见[项目路线图](../../ROADMAP.md)。
**范围：** `Real-time-Transcript` 仓库

## 1. 目标与非目标

### 目标

把 `Real-time-Transcript` 改造成一个面向留学生听课和课后复习的本地优先、云端可选实时转录工具：

1. 保证麦克风实时转录主流程可靠，降低首句延迟，避免丢失末尾音频。
2. 同一套产品同时支持本地模型和云端模型，低配轻薄本无需安装完整深度学习环境即可使用。
3. 让用户能在课堂中快速阅读字幕，在课后搜索、编辑、标记重点、添加笔记并导出。
4. 保持 `Auto-Subtitle-Generator-Standalone` 独立，主仓库不再包含它的运行时代码和依赖。
5. 保持现有 Python/Flask/Socket.IO 技术基础，降低迁移成本。

### 非目标

1. 本阶段不把两个 Git 仓库合并，也不通过 Git submodule 共享运行时代码。
2. 本阶段不实现说话人分离、多人声纹识别或课堂录音云端存储。
3. 本阶段不强制引入 Electron/Tauri 桌面壳。
4. 本阶段不要求云端 provider 使用某一家厂商的私有 SDK；云端接入通过可配置 provider 边界完成。

## 2. 用户场景与成功标准

### 课堂中

用户在浏览器打开本地服务，选择识别语言和运行方式，点击一次按钮开始听课。用户需要看到当前句子、已确认字幕、时间戳、运行模式和连接状态；模型变慢、网络短暂中断或麦克风权限失败时，界面必须给出可理解的处理建议。

### 课后复习

用户停止会话后，字幕自动保存为一个本地会话。用户可以搜索关键词，编辑识别结果，给句子加重点和笔记，按时间查看上下文，并导出 TXT、Markdown、VTT 或 SRT。

### 成功标准

- 在浏览器常见 44.1kHz/48kHz 麦克风下，后端得到正确的 16kHz 单声道音频。
- 每个 Socket.IO 连接最多存在一个活动转录会话；重复启动不会创建孤儿线程。
- 停止会话时，已进入缓冲区的尾部音频会被处理或明确标记为未处理，不静默丢弃。
- 本地模型未安装、显存不足或加载失败时，用户可以切换到云端模式；云端未配置时，用户仍能使用本地基础模式或看到明确提示。
- 页面不再调用不存在的 API；每个前端请求都有可测试的成功和错误路径。
- 主仓库删除 `Auto-Subtitle-Generator-Standalone/` 后，实时应用的安装和运行不依赖该目录。
- 新用户无需阅读文档即可完成“打开页面 → 选择语言 → 开始听课”主流程。
- 课堂中最重要的信息始终具有最高视觉层级；设置、错误和状态不会遮挡字幕内容。
- 关键交互支持键盘操作、清晰焦点态和足够的颜色对比度；窄屏下不出现横向滚动。

## 3. 方案与取舍

采用方案 B：保留 Flask、Flask-SocketIO 和原生前端，但拆分模型提供商、音频管线、会话生命周期和复习数据层。

### 为什么不选择渐进式小修

当前 `app.py` 是一个被大幅简化的实时服务，而 `realtime.html` 仍然保留翻译、Qwen、模型下载等旧接口调用。继续在单文件中补丁式添加功能，会让不存在的接口、模型加载和 UI 状态继续互相影响。

### 为什么不立即改成桌面应用

桌面壳可以改善系统级体验，但不能解决当前音频协议、模型 provider、会话清理和前后端契约问题。先建立稳定的浏览器应用边界，后续再封装为桌面应用时风险更低。

## 4. 总体架构

```text
浏览器
  ├─ AudioWorklet / fallback 音频采集
  ├─ Socket.IO 音频帧与字幕事件
  ├─ 听课视图
  └─ IndexedDB 会话与复习视图
          │
          ▼
Flask + Flask-SocketIO
  ├─ REST capability/config/session API
  ├─ Socket session manager
  ├─ 音频校验、重采样、缓冲、VAD/窗口策略
  ├─ TranscriptionProvider 工厂
  │    ├─ LocalWhisperProvider
  │    └─ CloudTranscriptionProvider
  └─ 本地/云端配置、错误映射、结构化日志
```

后端保持无状态 HTTP API；活动转录会话只保存在进程内，并以 Socket.IO `sid` 为生命周期边界。浏览器端负责保存可复习的会话数据，避免本阶段引入数据库和用户账户系统。

## 5. 后端模块边界

### `backend/config.py`

读取环境变量并生成不可变配置对象，包含：监听地址、端口、默认语言、默认模式、音频上限、云端 base URL、云端模型、API key 是否已配置。API key 只在后端读取，不通过 capability API 返回。

### `backend/device.py`

对现有 `gpu_detector.py` 做轻量封装，统一返回：

```python
DeviceProfile(
    device="cpu|cuda|mps",
    kind="cpu|nvidia|amd|apple",
    memory_gb: float | None,
    performance="limited|balanced|fast|unknown",
)
```

硬件探测只在服务启动时执行一次；明确区分“检测到硬件”和“PyTorch 实际可用”。不再通过危险的全局环境变量修改用户环境，也不硬编码不存在的显存。

### `backend/audio_pipeline.py`

定义统一音频帧结构并处理：

- base64 解码和大小校验；
- PCM 格式和采样率校验；
- 单声道转换；
- 任意浏览器采样率到 16kHz 的重采样；
- 有界队列和丢帧策略；
- 重叠窗口、尾部 flush 和可选静音判断。

后端不再假设所有客户端都以 16kHz 发送；客户端必须在每个启动会话中声明采样率，服务端以 payload 的采样率为准并验证合理范围。

### `backend/providers/base.py`

定义 provider 协议：

```python
class TranscriptionProvider(Protocol):
    def start(self, config: SessionConfig) -> None: ...
    def push(self, audio: np.ndarray) -> list[TranscriptSegment]: ...
    def flush(self) -> list[TranscriptSegment]: ...
    def close(self) -> None: ...
```

`TranscriptSegment` 至少包含 `id`、`text`、`start_ms`、`end_ms`、`is_final` 和可选 `confidence`。provider 不直接操作 Flask、Socket.IO 或 DOM。

### `backend/providers/local_whisper.py`

本地 provider 优先使用 `faster-whisper`；只有显式安装并选择兼容实现时才使用 `openai-whisper`。模型目录通过配置控制，避免前端传入任意路径。根据 `DeviceProfile` 和模型元数据给出建议，不在运行中盲目加载超出内存的模型。

本地失败时返回结构化错误：模型不存在、设备不可用、内存不足、依赖未安装、推理失败，供前端显示下一步建议。

### `backend/providers/cloud_transcription.py`

提供可配置的 OpenAI-compatible 云端转录边界。provider 负责：

- 从后端环境变量读取 base URL、API key 和 model；
- 将经过统一处理的音频窗口发送到云端；
- 将响应映射为 `TranscriptSegment`；
- 设置超时、重试上限和请求取消；
- 不记录原始音频或 API key；
- 把网络错误、鉴权错误、限流和服务端错误映射成稳定的错误码。

云端模式必须在 UI 上显示明确的隐私提示。云端请求失败时，不自动悄悄切回本地模型；只有用户选择“自动模式”且本地模型已就绪时，才允许回退，并在界面显示已切换。

### `backend/session_manager.py`

管理 `sid -> SessionState`：

- 创建、启动、停止和清理会话；
- 防止同一连接重复启动；
- 为每个会话维护队列、线程、provider、语言、采样率和统计信息；
- 发送 `transcription_started`、`transcript_segment`、`transcription_stopped` 和 `transcription_error`；
- 在 Socket.IO disconnect、异常和显式 stop 三种路径上执行幂等清理。

### `backend/routes.py`

提供最小、真实存在的 REST API：

```text
GET  /api/health
GET  /api/capabilities
GET  /api/models
GET  /api/config/public
```

删除当前前端依赖但后端不存在的 Qwen/翻译/模型下载接口；翻译和课后智能整理放到后续 provider 扩展，不伪装成已实现功能。

## 6. Socket.IO 契约

### 客户端到服务端

```text
start_transcription {
  mode: "auto" | "local" | "cloud",
  model: string | null,
  language: string,
  sample_rate: number,
  enable_vad: boolean
}

audio_chunk {
  audio: base64 PCM16,
  sample_rate: number,
  sequence: number
}

stop_transcription {}
```

### 服务端返回

所有启动和停止事件都必须返回 Socket.IO ack：

```json
{
  "status": "success",
  "session_id": "session-12",
  "provider": "local",
  "model": "small"
}
```

字幕事件统一使用：

```json
{
  "id": "segment-12",
  "text": "今天我们讨论监督学习。",
  "start_ms": 12000,
  "end_ms": 15300,
  "is_final": true,
  "sequence": 12
}
```

## 7. 前端产品结构

### `frontend/live-transcript.html`

课堂视图只保留高频操作：开始/停止、语言、模式、模型和翻译开关。页面显示当前识别句、历史字幕、连接/设备/云端状态、延迟、会话时长和字幕数量。

### `frontend/review.html`

课后视图包含会话列表、搜索、字幕编辑、重点标记和笔记。会话数据存储在 IndexedDB，结构为：

```javascript
{
  id,
  title,
  createdAt,
  durationMs,
  language,
  provider,
  segments: [{ id, text, startMs, endMs, note, starred }]
}
```

### `frontend/app.js`

按责任拆分为：socket 客户端、录音采集、字幕状态、会话持久化、导出、通知。所有 DOM 更新使用安全文本节点或转义函数，删除调试测试函数和内联 `onclick`。

### `frontend/styles.css`

移除对 Tailwind CDN 和 Google Fonts 的运行时依赖。采用本地 CSS 变量、响应式 grid、深色课堂主题和高对比度焦点状态。移动端保持单列布局，桌面端使用“控制侧栏 + 字幕主区”。

### 7.1 易用性与视觉质量要求

界面把“课堂中持续阅读”作为第一优先级，而不是把所有能力同时堆在设置面板中。

#### 信息架构

- 首屏只显示开始/停止、识别语言、运行方式和当前状态四组核心信息。
- 高级选项采用渐进披露：翻译、模型细节、音频保存和性能设置默认收起。
- “自动模式”是默认选项，但必须显示实际选择结果，例如“本地 · CPU”或“云端 · 已连接”。
- 课后复习独立为第二个视图，不让会话列表和笔记工具干扰实时字幕。

#### 课堂阅读体验

- 当前句子使用最大字号和最强对比度显示，历史字幕按时间顺序向下滚动。
- 临时字幕和最终字幕使用明确但克制的状态差异，避免频繁闪烁。
- 字幕区域支持自动跟随；用户向上滚动查看历史时暂停自动跟随，并提供“一键回到最新”按钮。
- 顶部状态栏显示运行模式、设备、延迟、会话时长和麦克风状态，避免用户猜测系统是否正常。
- 字幕文本不使用过度装饰、渐变或动画；动画仅用于录音状态、加载和状态切换。

#### 美观与一致性

- 采用深色蓝黑课堂主题，使用单一强调色表示可操作状态，错误使用暖色，成功使用绿色。
- 建立统一的设计令牌：背景、面板、边框、文字层级、圆角、间距、阴影、焦点环和状态颜色。
- 桌面布局保持稳定的控制栏宽度和字幕阅读宽度，避免超宽屏上文本行过长。
- 空状态、加载状态、无权限状态、模型不可用状态、云端未配置状态和网络错误状态都要有专门的视觉设计和下一步操作。
- 不依赖 Emoji 作为主要图标；使用统一的内置 SVG 图标，并为图标按钮提供可见或屏幕阅读器可读的名称。

#### 可访问性与响应式

- 正文和控件满足 WCAG AA 级别的对比度目标。
- 所有按钮、选择框、对话框和状态消息支持键盘操作，焦点不可被背景色吞掉。
- 开始、停止、错误和云端隐私提示通过 `aria-live` 及时播报。
- 最小触摸目标为 44px；窄屏采用单列布局，字幕字号和控制按钮不会挤压到不可用。
- 支持 `prefers-reduced-motion`，减少动画对敏感用户的影响。

#### 交互反馈

- 不使用浏览器原生 `alert()` 或 `confirm()` 作为主要反馈方式。
- 长耗时操作显示进度、当前阶段和取消/停止入口。
- 错误消息使用“发生了什么 + 为什么 + 下一步怎么做”的结构，避免只显示“请求失败”。
- 破坏性操作如清空会话需要页面内确认，但停止录音不应被多余确认阻塞。

## 8. 会话与复习交互

1. 页面加载时读取 capability，显示硬件和云端可用性。
2. 用户选择“自动”时，前端展示推荐理由，后端最终决定 provider。
3. 开始成功后创建本地会话草稿，每收到最终字幕就写入 IndexedDB，写入失败只提示用户，不影响实时转录。
4. 临时字幕只更新当前句，不立即写入历史记录。
5. 停止成功后执行 provider flush，把尾部最终字幕写入会话并标记完成。
6. 复习页按 `startMs` 排序，搜索只匹配文本和笔记。
7. 导出使用真实字幕时间戳；没有音频文件时仍能导出字幕和 Markdown 笔记。

## 9. 错误处理与隐私

错误统一包含 `code`、`message` 和可选 `action`，例如：

```json
{
  "code": "CLOUD_NOT_CONFIGURED",
  "message": "云端模型尚未配置",
  "action": "请在 .env 中设置 CLOUD_API_KEY，或切换到本地模式"
}
```

要求：

- 不使用裸 `except:`；记录可诊断日志但不把堆栈直接返回浏览器。
- 限制单个音频帧大小、队列长度和云端请求体大小。
- 默认绑定 `127.0.0.1`；若用户显式绑定局域网，启动日志提示风险。
- 默认关闭 CORS 全开放配置，允许通过环境变量配置来源。
- 默认关闭 Flask debug 模式。
- 不把音频、API key 或完整字幕写入普通日志。

## 10. 依赖与运行方式

将当前单一依赖文件拆为：

```text
requirements-core.txt    # Flask、Socket.IO、numpy、dotenv
requirements-local.txt   # torch、faster-whisper、可选 whisper
requirements-cloud.txt   # httpx/requests 等云端请求依赖
requirements-dev.txt     # pytest、ruff、测试工具
```

提供三种清晰启动方式：

```bash
./start.sh --mode auto
./start.sh --mode local
./start.sh --mode cloud
```

其中 `auto` 不代表自动上传音频；它只根据本地可用性和用户配置选择 provider，并在 UI 中展示结果。

## 11. 测试策略

### 单元测试

- 采样率转换：16k、44.1k、48k、空帧和异常采样率。
- 音频帧校验：非法 base64、超大 payload、错误 PCM 长度。
- session manager：重复启动、幂等停止、disconnect 清理、provider 异常。
- provider 工厂：local/cloud/auto 选择和错误码。
- 字幕格式化：重叠窗口去重、尾部 flush、真实 VTT/SRT 时间戳。
- 设备推荐：CPU、CUDA、MPS 的模拟结果，不依赖真实 GPU。

### API 与 Socket 测试

使用 Flask test client 和 Socket.IO test client，不要求安装真实模型。模型推理通过 fake provider 注入，验证：

- 启动和停止 ack；
- 音频帧流转为字幕事件；
- provider 错误映射；
- capability 返回不泄露 API key。

### 浏览器验收

使用 Playwright 或手动浏览器验收：

- 首次打开和无云端配置状态；
- 开始/停止/重复点击；
- 44.1kHz 与 48kHz 音频输入；
- 断线重连；
- 搜索、编辑、重点、笔记和导出；
- 移动端单列布局。
- 新用户首次操作无需阅读说明即可开始转录；
- 高级设置收起/展开不影响字幕阅读；
- 自动跟随、暂停跟随和回到最新交互；
- 空状态、加载状态、错误状态和云端隐私提示的视觉反馈；
- 键盘导航、焦点态、屏幕宽度变化和减少动画偏好。

## 12. 实施顺序

1. 删除 `Auto-Subtitle-Generator-Standalone/` 及实时仓库中的过时引用，建立新的目录和依赖边界。
2. 先为音频、会话和 provider 契约写测试，再实现后端基础模块。
3. 修复 Socket.IO ack、音频采样率、队列生命周期和模型错误处理。
4. 加入本地 provider 与 capability API。
5. 加入云端 provider 和配置说明。
6. 重做听课页面并完成 IndexedDB 复习页。
7. 补齐导出、安装文档、测试和运行验收。

每一步都保持可运行；离线字幕生成器仓库不被本项目导入，也不参与实时应用安装。
