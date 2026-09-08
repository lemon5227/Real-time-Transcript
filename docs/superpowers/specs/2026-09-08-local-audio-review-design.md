# 本地原声课后复习设计

**日期：** 2026-09-08  
**状态：** 已确认设计方向，待实现计划与代码实现  
**适用项目：** RealTimeTranscript / EchoNote

## 1. 背景与问题

EchoNote 当前使用浏览器麦克风采集音频：一条 PCM 路径将音频发送给本地或云端转录服务，课堂结束后只把字幕、课堂元数据保存到浏览器 IndexedDB。这样可以课后阅读和编辑字幕，但无法回听老师的原声，也无法方便核对专有名词、口音和模型识别错误。

本功能在不耦合本地模型与云端模型的前提下，为每节课默认保存一份本地原声，并在课后复习页中实现音频与字幕时间轴联动。

## 2. 目标

1. 默认保存课堂原声，且用户在开始听课前能清楚看到保存状态和隐私边界。
2. 原声只保存在当前浏览器/设备的本地存储中；云端模式只发送实时转录所需的音频，不建立云端录音归档。
3. 长课按片段持续保存，尽量降低浏览器崩溃或中途异常导致整节录音丢失的风险。
4. 复习页支持播放、暂停、拖动进度、点击字幕跳转，以及播放时高亮当前字幕。
5. 音频保存失败时不阻断转录和字幕保存，并向用户明确显示“字幕已保存、录音未完整保存”等可操作状态。
6. 存储实现与 UI、转录提供方解耦，为以后导出、重新转录、云端同步、移动端导入和说话人识别保留接口。

## 3. 非目标

本次不实现以下内容：

- 云端录音备份或跨设备同步。
- 服务端音频文件管理 API。
- MP3/M4A 等格式转码。
- 自动摘要、章节识别、说话人分离等上层 AI 功能。
- 自动写入用户可见的本机文件路径。浏览器必须获得用户授权才能写入用户文件系统，因此第一版采用浏览器私有本地存储，并提供手动导出入口。

## 4. 方案比较与选择

### 方案 A：课堂结束后保存一个完整 Blob

实现最简单，但整节课的音频需要保存在内存中，长时间课程占用较高；如果标签页崩溃或设备异常，可能丢失还没有结束保存的全部录音。因此不采用。

### 方案 B：MediaRecorder 分片 + IndexedDB

浏览器用 `MediaRecorder` 定期产生音频片段，逐片写入 IndexedDB。它兼容现有网页架构，且实现成本适中，因此作为必需的兼容回退方案。

### 方案 C：录音资产层 + OPFS 主存储 + IndexedDB 回退

采集、存储和复习页面之间通过统一的 `AudioRepository` 接口连接。支持 OPFS 时，把音频分片写入当前网页来源私有的本地文件空间；不支持时回退到 IndexedDB 分片。课堂元数据和分片索引始终保存在 IndexedDB。

选择方案 C。OPFS 面向网页来源私有文件存储，适合较大的本地文件和高性能读写，但仍受浏览器配额约束；`navigator.storage.estimate()` 可用于显示当前使用量和估算配额。因此它比直接把音频 Blob 放进课堂记录更适合长课，也不会让复习页绑定某一种存储实现。

## 5. 整体架构

```text
同一个 MediaStream
       ├── Web Audio / PCM ──> Socket.IO ──> 本地或云端转录
       └── MediaRecorder ────> AudioRepository
                                      ├── OPFS（优先）
                                      └── IndexedDB（回退）

课堂字幕 + 音频清单 ──> IndexedDB sessions
       │
       └── 课后复习页：播放器 <-> 字幕时间轴
```

### 5.1 采集层

在现有 `beginCapture()` 中复用已经授权的 `state.stream`，不再额外申请一次麦克风权限。PCM 采集、音量检测和实时转录路径保持不变；新增 `MediaRecorder` 只负责产生原声编码片段。

录音器启动时按照浏览器能力选择 MIME 类型，例如优先选择浏览器支持的 Opus 容器，无法使用时选择其他浏览器支持的音频容器。录音记录必须保存实际使用的 `mimeType`，不能假设所有浏览器都使用同一种扩展名。

使用约 10 秒的 `timeslice` 生成分片。由于浏览器的 `dataavailable` 事件可能延迟，分片元数据同时记录：

- `sequence`：从 0 开始的严格递增序号。
- `startMs` / `endMs`：相对于本节录音开始的客户端单调时间。
- `bytes`：分片大小。
- `mimeType`：实际编码格式。

写入任务按 `sequence` 串行排队，防止异步 IndexedDB/OPFS 写操作乱序。

### 5.2 AudioRepository 接口

新增独立的录音存储模块，不让 `app.js` 或 `review.js` 直接操作 OPFS/IndexedDB 细节：

```javascript
AudioRepository.createRecording({
  sessionId,
  mimeType,
  startedAt
}) -> Promise<AudioManifest>

AudioRepository.appendChunk({
  sessionId,
  sequence,
  blob,
  startMs,
  endMs
}) -> Promise<void>

AudioRepository.finalizeRecording({
  sessionId,
  durationMs
}) -> Promise<AudioManifest>

AudioRepository.getManifest(sessionId) -> Promise<AudioManifest | null>
AudioRepository.getPlayableBlob(sessionId) -> Promise<Blob | null>
AudioRepository.deleteRecording(sessionId) -> Promise<void>
AudioRepository.getUsage() -> Promise<{usage, quota} | null>
```

实现要求：

- `createRecording()` 创建 `recording` 状态的清单，重复调用不能覆盖已有课堂的其他音频。
- `appendChunk()` 必须幂等：同一个 `sessionId + sequence` 重复写入不会产生重复播放数据。
- `finalizeRecording()` 只有在最后一个分片写入完成后才将状态改为 `ready`。
- 存储异常时清单状态可以变为 `partial` 或 `failed`，但不能删除已保存的字幕。
- `deleteRecording()` 删除清单及其所有分片；课堂删除流程必须调用它。
- `getPlayableBlob()` 按 `sequence` 排序组合 Blob，并使用清单中的 MIME 类型返回。
- 未来可增加 `createSyncSource()` 或 `exportRecording()`，但第一版不实现网络同步。

### 5.3 存储布局

IndexedDB 数据库版本升级到下一版本，保留现有 `sessions` store，并增加音频相关 store/清单字段。

每条课堂记录增加：

```javascript
audio: {
  version: 1,
  enabled: true,
  status: "recording" | "ready" | "partial" | "failed" | "unavailable",
  storage: "opfs" | "indexeddb" | "none",
  mimeType: "audio/webm;codecs=opus",
  extension: "webm",
  chunkCount: 0,
  bytes: 0,
  durationMs: 0,
  startedAt: "2026-09-08T...Z",
  completedAt: null,
  error: ""
}
```

OPFS 实现使用每节课独立目录和有序分片文件，目录名只使用内部 `sessionId`，不使用课程标题等用户输入。IndexedDB 回退实现使用独立的 `audioChunks` store，键由 `sessionId` 和 `sequence` 组成。两种后端都只通过 `AudioRepository` 暴露。

### 5.4 课堂生命周期

1. 开始前：默认开启“保存原声”，检查 `MediaRecorder`、本地存储能力和可用空间；如果不能保存，阻止默认录音模式静默失败，并提供“仅保存字幕并继续”的明确选择。
2. 转录服务启动成功后：用服务返回的 `session_id` 创建录音清单，再启动 `MediaRecorder`。
3. 上课中：每次收到 `dataavailable` 事件就写入一个分片；字幕继续按现有逻辑自动保存。界面显示“原声保存中”和已保存状态。
4. 结束时：先请求录音器输出最后一片，等待录音器停止和所有分片写入完成，再发送/完成转录停止流程。最终课堂记录同时保存字幕和音频清单。
5. 中断时：释放麦克风和实时连接；已写入的分片保留，清单标记为 `partial`，复习页仍允许播放已保存部分并提示不完整。
6. 删除时：先删除音频资产，再删除课堂记录；任一部分失败都显示明确错误，避免假装完成删除。

停止事件和 Socket.IO 回调可能同时到达，结束流程必须保持幂等，只允许一次最终保存和一次资源释放。

## 6. 复习页体验

选中课堂后，在字幕统计工具栏下方增加音频区域：

- `<audio controls>` 播放器，显示播放/暂停、进度和音量控制。
- 状态徽标：原声已保存、正在恢复、部分保存、未保存。
- 显示音频格式、时长和大小；无法恢复时保留字幕阅读功能。
- 点击某段字幕时，将播放器 `currentTime` 定位到该段 `startMs / 1000`。
- 播放过程中根据 `currentTime` 找到 `startMs <= time < endMs` 的字幕，添加当前高亮；滚动只在用户开启自动跟随时执行。
- 导出菜单增加“原声”选项，文件名使用课堂标题，扩展名根据清单 MIME 类型决定。
- 替换选中课堂时释放旧的 `URL.createObjectURL()`，避免长时间浏览多节课造成内存泄漏。

复习页不依赖实时转录服务；只要本地课堂清单和音频资产还在，即使后端没有启动也可以复习。

## 7. 设置、隐私和可靠性

- 设置项名称为“保存原声”，默认开启，偏好保存在现有本地设置中。
- 开始听课前显示“原声仅保存在本机浏览器”的说明；云端模式额外说明实时处理会向已配置的云服务发送音频。
- 不新增服务端音频上传接口，不把录音写入 Git、日志或普通服务器临时目录。
- 课堂删除必须连带删除音频；提供单独的“导出原声”作为浏览器存储之外的备份方式。
- 通过 `navigator.storage.estimate()` 显示本地存储使用情况；空间不足时在课中提示，并继续保留转录文本。
- 浏览器不支持录音时，默认不允许无提示地进入“已保存原声”状态；用户必须明确选择仅字幕模式。
- 清除网站数据、使用无痕窗口或浏览器回收站点存储可能删除录音。界面文案必须说明这一限制，不能承诺永久保存。

## 8. 测试与验收标准

### 自动化测试

- `AudioRepository` 能创建清单、按序写入分片、完成清单、读取可播放 Blob，并在删除后清理全部分片。
- 重复写入相同序号不会重复音频；乱序到达的分片读取时按序排列。
- OPFS 不可用时自动使用 IndexedDB 回退；两种实现共享同一套接口行为。
- 音频写入失败时，课堂字幕仍能保存，课堂状态为 `partial` 或 `failed`，错误信息可见。
- 录音结束流程等待最后分片写入完成，且重复停止事件不会重复保存或释放资源。
- `normalizeSession()` 能保留和兼容音频清单字段；旧的无音频课堂仍能正常显示和复习。
- 复习页能加载播放器、点击字幕定位、根据播放进度高亮字幕，并在切换课堂时释放旧的 Blob URL。
- 删除课堂会同时调用音频清理逻辑；没有音频的旧课堂删除行为不受影响。

### 浏览器验收

1. 在本地模型模式启动一节短课，确认默认“保存原声”开启，录音状态持续显示。
2. 在云端模式启动一节短课，确认仍保存本机原声，同时隐私提示明确说明云端实时处理。
3. 结束课堂，刷新页面并打开复习页，确认字幕和原声都能恢复。
4. 播放音频，点击不同字幕，确认播放位置跳转；播放经过字幕时确认当前字幕高亮。
5. 删除课堂，确认复习列表和本地音频资产都被删除。
6. 模拟存储失败或不支持 `MediaRecorder`，确认可以保留字幕，并明确显示原声未保存原因。
7. 运行完整 Python 测试、前端语法检查、静态检查和 `git diff --check`。

## 9. 未来扩展边界

`AudioManifest` 和 `AudioRepository` 是后续功能的稳定边界：

- 重新选择模型对已有录音执行转录。
- 对原声做术语纠错、章节切分和摘要。
- 增加说话人分离或多声道录音。
- 将手机录音或其他设备的音频导入同一资产格式。
- 增加显式的云端同步适配器，而不改变复习页和课堂记录格式。
- 增加用户选择的保存期限、批量导出和磁盘空间管理。
- 在桌面壳或 PWA 中接入更稳定的文件/后台任务存储。

这些扩展都不需要让本地模型和云端模型共享实现；它们只消费统一的音频资产和字幕时间轴。
