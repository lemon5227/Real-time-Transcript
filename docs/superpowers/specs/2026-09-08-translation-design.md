# 课堂翻译设计

**日期：** 2026-09-08  
**状态：** 已确认设计方向，待实现  
**适用项目：** RealTimeTranscript / EchoNote

## 1. 背景

RealTimeTranscript 面向留学生听课和课后复习。转录保留老师的原文，但语言障碍会让用户在课堂上错过关键句子，课后也需要花时间逐段查词。本功能增加两条互补路径：课堂中的低延迟临时翻译，以及课后的高质量精确翻译。

翻译必须与原始音频保存、本地转录和云端转录解耦。翻译服务只接收已经生成的文本，不接收或保存课堂原声。

## 2. 目标

1. 实时转录产生最终字幕后，可按句或小批次快速翻译，尽量不阻塞原文转录。
2. 快速翻译支持 Google Cloud Translation 和 Microsoft Translator，并允许用户选择服务。
3. 精确翻译支持本地模型；本地模型不可用或设备性能不足时，支持已配置的云端模型。
4. 课后可以翻译整节课、选中段落或单句，并保存翻译结果供复习和重试。
5. 原文始终保留，翻译失败只影响翻译层，不影响字幕、原声保存和课堂结束流程。
6. API 密钥只存在后端环境变量；浏览器和日志不暴露密钥、完整请求凭据或原始音频。
7. 用统一 provider 接口隔离 Google、Microsoft、本地模型和云端模型，未来可以增加其他服务而不改动页面数据结构。

## 3. 非目标

- 不在浏览器内直接保存或上传原始音频给翻译服务。
- 不把 Google/Microsoft API Key 放到前端，也不允许用户在公开 URL 中传密钥。
- 本次不承诺自动识别所有语言的最佳翻译模型；源语言和目标语言由设置及转录配置提供。
- 不在实时翻译路径中做全文重写、摘要或术语库训练。

## 4. 用户体验

### 4.1 设置

翻译设置放入课堂设置和全局偏好中：

- “实时翻译”：关闭 / 快速翻译 / 精确翻译 / 自动。
- “快速翻译服务”：Google / Microsoft。
- “精确翻译方式”：本地模型 / 云端模型 / 自动。
- “目标语言”：默认中文，可扩展为常用语言列表。

实时翻译默认关闭，避免用户没有明确选择时增加课堂网络请求和设备负载。用户开启后，默认使用快速翻译。自动模式只在精确翻译中使用：本地模型已就绪且设备能力允许时优先本地，否则使用已配置的云端模型；不能静默把用户选择的模型翻译改成 Google/Microsoft。

课堂开始前显示实际路径，例如“实时翻译：Microsoft 快速翻译”或“精确翻译：本地模型”。没有对应凭据或模型时，在开始前给出可操作提示，而不是开始后才静默失败。

### 4.2 实时课堂

实时字幕仍然先显示原文。只有最终字幕进入翻译队列，临时字幕不翻译，防止同一句话反复消耗请求。翻译结果显示在原文下方，使用轻量的状态标识区分“翻译中”“快速翻译”“模型翻译”和“翻译失败”。

队列以 3–5 个最终句子或约 3,000 个字符为一批，按顺序发送并限制并发数为 1。每个结果都携带 `segment_id`，因此延迟返回、断线重连或字幕更新不会把翻译插到错误的句子下。翻译请求只包含段落文本、源语言、目标语言和 provider 所需的必要参数。

翻译服务短暂失败时，原文继续滚动；界面提供“重试翻译”，不重启转录。实时队列在课堂结束时停止接收新任务，但已经发出的请求可以完成或标记为未完成。

### 4.3 课后复习

复习页保留原文搜索、编辑和音频联动，同时提供：

- “翻译整节课”：显示进度，可暂停、继续和重试。
- “翻译选中内容”：只翻译用户选择的段落。
- “翻译本句”：单句快速重试或切换到精确模式。
- 翻译结果与课堂记录一起缓存到 IndexedDB；切换课堂不重复请求已有结果。
- 导出 TXT、Markdown、VTT、SRT 时可以选择仅原文、原文+译文或仅译文。

课后精确翻译默认按用户选择的精确翻译方式执行，不会把整节课自动发送到云端，除非用户选择云端或自动模式明确回退到云端。页面显示预计文本量和当前 provider，避免用户误以为课后操作完全离线。

## 5. 架构

```text
最终 TranscriptSegment
        │
        ├── 原文保存 / 音频时间轴
        └── TranslationQueue（实时小批次）
                  │
                  ▼
          TranslationRouter
          ├── GoogleFastProvider
          ├── MicrosoftFastProvider
          ├── LocalModelProvider
          └── CloudModelProvider
                  │
                  ▼
       translation_result(segment_id, target, text, metadata)
```

后端新增 provider 协议和 router。HTTP/Socket.IO 层只负责校验请求、关联会话和返回结构化结果，页面不感知 Google 与 Microsoft 的具体请求格式。

### 5.1 Provider 接口

```python
class TranslationProvider(Protocol):
    name: str
    mode: str  # fast | model

    def translate_batch(
        self,
        texts: list[str],
        source_language: str,
        target_language: str,
    ) -> list[str]: ...
```

所有 provider 都必须：

- 保持输入顺序，返回数量与输入一致；
- 对空文本和超出请求限制的文本进行服务层校验；
- 将鉴权失败、限流、超时、网络错误和无效响应映射为不含密钥的结构化错误；
- 不记录完整原文、翻译文本、API Key 或 Authorization header；
- 由 router 统一设置超时和重试上限，避免实时翻译线程无限等待。

Google provider 调用 Cloud Translation `translateText`，将多个文本作为 `contents` 批量发送。Microsoft provider 调用 Translator Text REST `translate`，把文本映射为其 `body` 元素。两者的请求格式、区域 header、限制和响应解析只存在于各自 provider 中。

精确翻译 provider 复用本项目已有的本地/云端模型配置边界，但使用独立的翻译模型配置和错误状态。第一版提供可注入的本地模型适配器和 OpenAI-compatible 云端适配器；没有配置模型时明确返回 `TRANSLATION_NOT_CONFIGURED`。

### 5.2 Router 选择规则

```text
实时：
  off       -> 不创建翻译队列
  fast      -> 用户选择 Google 或 Microsoft
  precise   -> local / cloud / auto
  auto      -> local ready + device capable ? local : configured cloud model

课后：
  用户选择 fast / precise
  已有缓存 -> 直接显示
  没有缓存 -> 分批请求，逐批保存和更新进度
```

“自动”只代表在本地精确模型和云端精确模型之间选择，选择结果在课堂开始前返回给前端并展示。快速翻译 provider 不作为精确模型的无提示回退。

### 5.3 数据结构

在不破坏旧课堂记录的前提下，`TranscriptSegment` 的前端归一化结果增加：

```javascript
translations: {
  zh: {
    text: "……",
    status: "ready" | "pending" | "failed",
    mode: "fast" | "model",
    provider: "google" | "microsoft" | "local" | "cloud",
    model: "",
    updatedAt: "2026-09-08T...Z",
    error: ""
  }
}
```

使用语言代码作为 key，允许将来同一课堂保存多种目标语言。旧记录缺少 `translations` 时归一化为空对象。实时事件使用：

```json
{
  "segment_id": "segment-12",
  "target_language": "zh",
  "text": "……",
  "status": "ready",
  "mode": "fast",
  "provider": "microsoft",
  "model": null
}
```

## 6. 接口与隐私

实时翻译使用现有 Socket.IO 会话：`translate_segments` 发送段落 ID 和文本，`translation_result` 返回逐条结果。后端只接受当前 Socket 会话中的 segment ID，避免跨会话写入。

课后翻译使用受控的 HTTP endpoint，支持一批段落并返回逐条结果；服务端限制单批字符数和段落数量，并按请求中的 `session_id` 只处理浏览器提交的文本。后端不把课后文本写入持久化日志。

Google/Microsoft 凭据通过环境变量配置，`/api/capabilities` 只返回 `configured`、provider 名称和可用模式，不返回 key。云端模型也沿用后端 API Key 配置。前端只收到 provider 状态、模型显示名和错误码。

翻译不是录音归档：音频仍按本地原声设计保存；云端转录的音频路径与翻译文本路径分开说明。

## 7. 测试与验收标准

### 自动化

- Google/Microsoft provider 请求体和响应解析正确，输入顺序保持不变，密钥不进入异常消息或日志。
- HTTP 错误、超时、限流和无效响应映射为稳定错误码。
- router 根据 fast/model/auto 和本地可用性选择正确 provider；缺少配置时不伪装成成功。
- 实时队列只消费最终字幕，批量、去重并限制并发；结果按 segment ID 合并。
- 课后翻译按批次缓存，失败重试不会清掉已经成功的结果。
- 旧课堂没有 `translations` 字段时仍可打开、播放、编辑和导出。
- 前端能显示原文、译文和翻译状态，翻译失败不影响转录停止和课堂保存。

### 浏览器验收

1. 关闭实时翻译时，课堂请求和界面都不创建翻译任务。
2. 选择 Google 或 Microsoft 快速翻译，最终字幕出现译文，原文和原声流程不受影响。
3. 选择本地精确翻译并在模型就绪时运行，确认文本不离开本机；本地不可用时自动模式明确显示云端回退。
4. 课后翻译整节课，刷新页面后已完成译文仍存在；失败批次可重试。
5. 导出时分别验证原文、原文+译文和仅译文三种内容。
6. 没有配置任何翻译服务时，用户能继续只使用转录和原声复习。

## 8. 未来扩展

- 术语表、课程专有名词和用户纠正记忆。
- 对译文进行模型精修、段落重排和学习卡片生成。
- 说话人/章节级上下文翻译。
- DeepL、其他兼容 REST 服务和自托管翻译模型。
- 多语言同时显示以及字幕显示密度控制。
