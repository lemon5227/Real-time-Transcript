# Real-time Transcript 中文指南

这是面向留学生的实时课堂转录与课后复习工具。进入课堂前启动服务，选择本地、云端或自动路径；下课后在复习页搜索字幕、补充笔记、标记重点并导出。

## 快速启动

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-core.txt
cp .env.example .env
./start.sh --mode auto
```

本地模型按设备分开：Apple Silicon Mac 安装 `requirements-mac.txt` 走 MLX Parakeet，Windows/Linux/Intel Mac 安装 `requirements-local.txt`；检测到 NVIDIA 独显时标准 Whisper 自动走 CUDA。轻薄 CPU 本可使用 `requirements-cloud.txt` 配置云端，自动模式会在本地不适合时使用云端。Mac 上中文课堂请直接切换云端，避免偷偷退回 CPU Whisper。首次进入课堂页后，可在“上课前检查”中查看 MLX/CUDA/CPU、模型状态并测试麦克风。完整说明请看主 [README](README.md) 与 [QUICKSTART](QUICKSTART.md)。

课堂页：`http://127.0.0.1:5001/` · 复习页：`http://127.0.0.1:5001/review`

## 隐私

原声默认按 10 秒片段保存在当前浏览器本机，云端转录只会把实时处理所需的音频窗口发送到 `.env` 中配置的服务，不建立云端录音归档。翻译是独立的文本路径：可选 Google Cloud 或 Microsoft 做快速翻译，也可使用 Ollama/LM Studio 等 OpenAI-compatible 本地服务，或使用云端模型做精确翻译；翻译不会上传原声。API Key 只在后端环境变量中保存。课堂右侧的“更多设置”可以查看模型状态，并在上课前提前下载本地模型。详见 [`docs/PRIVACY.md`](docs/PRIVACY.md)。

实时翻译默认关闭。快速翻译默认会尝试 Google 公共通道（免 Key，但不保证稳定）；需要更稳定的官方通道时，在 `.env` 配置 `TRANSLATION_GOOGLE_*` 或 `TRANSLATION_MICROSOFT_*`。需要本地模型精翻时配置 `TRANSLATION_LOCAL_BASE_URL` 和 `TRANSLATION_LOCAL_MODEL`，需要云端模型精翻时配置 `TRANSLATION_CLOUD_*`。没有翻译配置也不影响转录、原声保存和课后复习。
