# Real-time Transcript

面向留学生听课和课后复习的实时语音转录工作台。应用优先在本地运行，也支持把音频窗口发送到用户配置的云端转录服务。

## 能做什么

- 麦克风实时转录，支持中文、英文、日语、韩语等课堂语言。
- 自动处理 44.1kHz/48kHz 麦克风并统一为 16kHz 单声道音频。
- 本地模式使用 `faster-whisper`；轻薄本可以选择云端模式，避免安装大型本地模型。
- 自动模式根据本地模型和云端配置选择 provider，并在界面中显示实际选择结果。
- 课后复习支持本地会话保存、搜索、编辑、重点标记、笔记和 TXT/Markdown/VTT/SRT 导出。

## 安装

核心环境（只提供服务和云端接入边界）：

```bash
pip install -r requirements-core.txt
```

云端模式：

```bash
pip install -r requirements-cloud.txt
cp .env.example .env
```

本地模式：

```bash
pip install -r requirements-local.txt
cp .env.example .env
```

开发和测试：

```bash
pip install -r requirements-dev.txt
```

系统只需要 Python 3.9+。使用本地模型时，`faster-whisper` 会根据实际环境选择 CPU、CUDA 或 Apple Silicon 支持；云端模式不导入 torch。

## 启动

```bash
./start.sh --mode auto
./start.sh --mode local
./start.sh --mode cloud
```

打开 `http://127.0.0.1:5001/` 开始听课，打开 `http://127.0.0.1:5001/review` 进入课后复习。

云端模式需要在后端 `.env` 设置：

```dotenv
CLOUD_BASE_URL=https://api.example.com/v1
CLOUD_API_KEY=your-key
CLOUD_TRANSCRIPTION_MODEL=your-transcription-model
```

API Key 只保存在后端环境变量中，不会返回给浏览器或写入日志。选择云端模式时，页面会明确提示音频将发送到配置的服务。

## API

- `GET /api/health`：服务健康状态。
- `GET /api/capabilities`：设备、本地模型和云端配置能力，不返回密钥。
- `GET /api/models`：本地模型目录和可用状态。
- `GET /api/config/public`：前端可展示的非敏感配置。
- Socket.IO：`start_transcription`、`audio_chunk`、`stop_transcription`。

详细契约见 [`docs/API.md`](docs/API.md)，隐私说明见 [`docs/PRIVACY.md`](docs/PRIVACY.md)。

离线视频字幕生成器已经独立维护在 [Auto-Subtitle-on-Generative-AI](https://github.com/lemon5227/Auto-Subtitle-on-Generative-AI)，本项目不导入它的运行时代码或依赖。
