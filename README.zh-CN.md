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

本地模型另装 `pip install -r requirements-local.txt`；轻薄本或内存不足时装 `requirements-cloud.txt` 并配置云端变量。首次进入课堂页后，可在“上课前检查”中查看模型缓存、下载 Tiny/Small 并测试麦克风。完整说明请看主 [README](README.md) 与 [QUICKSTART](QUICKSTART.md)。

课堂页：`http://127.0.0.1:5001/` · 复习页：`http://127.0.0.1:5001/review`

## 隐私

原声默认按 10 秒片段保存在当前浏览器本机，云端转录只会把实时处理所需的音频窗口发送到 `.env` 中配置的服务，不建立云端录音归档。翻译是独立的文本路径：可选 Google Cloud 或 Microsoft 做快速翻译，也可在课后使用本地/云端模型做精确翻译；翻译不会上传原声。API Key 只在后端环境变量中保存。详见 [`docs/PRIVACY.md`](docs/PRIVACY.md)。

实时翻译默认关闭。需要快速翻译时，在 `.env` 配置 `TRANSLATION_GOOGLE_*` 或 `TRANSLATION_MICROSOFT_*`；需要模型精翻时配置 `TRANSLATION_CLOUD_*`。没有翻译配置也不影响转录、原声保存和课后复习。
