# Real-time Speech Transcription

This project provides a **Real-time Speech Transcription** service using WebSockets and AI models (Whisper, SenseVoice).

> **Note**: The offline Subtitle Generation features have been moved to a separate project: `Auto-Subtitle-Generator-Standalone`.

## Features
- Real-time audio streaming via WebSocket.
- Transcription using Whisper, Faster-Whisper, or SenseVoice.
- Web Interface (`realtime.html`).

## Usage

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Start the server:
   ```bash
   ./start_realtime.sh
   ```
3. Open `http://localhost:5001`.
