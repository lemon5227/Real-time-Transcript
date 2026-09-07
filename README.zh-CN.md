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

本地模型另装 `pip install -r requirements-local.txt`；轻薄本或内存不足时装 `requirements-cloud.txt` 并配置云端变量。完整说明请看主 [README](README.md) 与 [QUICKSTART](QUICKSTART.md)。

课堂页：`http://127.0.0.1:5001/` · 复习页：`http://127.0.0.1:5001/review`

## 隐私

本地模式不上传音频；云端模式会把音频窗口发送到 `.env` 中配置的服务。API Key 只在后端环境变量中保存。详见 [`docs/PRIVACY.md`](docs/PRIVACY.md)。
