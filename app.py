from flask import Flask, request, jsonify, send_file
from flask_socketio import SocketIO, emit
from flask_cors import CORS
import queue
import threading
import time
import base64
import os
import shutil
import numpy as np
import torch
import whisper
try:
    from faster_whisper import WhisperModel as FasterWhisperModel
    FASTER_AVAILABLE = True
except ImportError:
    FASTER_AVAILABLE = False

try:
    from transformers import AutoModel, AutoTokenizer
    # Also try FunASR if available for better SenseVoice support
    try:
        from funasr import AutoModel as FunASRAutoModel
        FUNASR_AVAILABLE = True
    except Exception:
        FUNASR_AVAILABLE = False
except ImportError:
    pass

try:
    from opencc import OpenCC
    # Convert Traditional Chinese to Simplified Chinese
    converter = OpenCC('t2s')
    OPENCC_AVAILABLE = True
except ImportError:
    converter = None
    OPENCC_AVAILABLE = False

from gpu_detector import get_optimal_device, create_device_environment

# Apply device optimization environment variables
device_env = create_device_environment()
for key, value in device_env.items():
    os.environ[key] = value

DEVICE, _ = get_optimal_device()
print(f"Using device: {DEVICE}")

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")

# Global variables for real-time transcription
realtime_models = {}
realtime_audio_queues = {}
realtime_threads = {}
realtime_locks = {}

SENSEVOICE_AVAILABLE = True # Assume true or check imports
DISTIL_AVAILABLE = True # Check imports

# Resample audio util
def resample_audio(audio_array: np.ndarray, src_rate: int, target_rate: int = 16000) -> np.ndarray:
    if audio_array.size == 0:
        return audio_array.astype(np.float32, copy=False)
    if src_rate == target_rate:
        return audio_array.astype(np.float32, copy=False)
    src_rate = float(src_rate)
    target_rate = float(target_rate)
    target_length = max(1, int(round(audio_array.shape[0] * target_rate / src_rate)))
    if target_length == audio_array.shape[0]:
        return audio_array.astype(np.float32, copy=False)
    x_old = np.linspace(0.0, 1.0, num=audio_array.shape[0], endpoint=False)
    x_new = np.linspace(0.0, 1.0, num=target_length, endpoint=False)
    resampled = np.interp(x_new, x_old, audio_array.astype(np.float32, copy=False))
    return resampled.astype(np.float32, copy=False)

def get_realtime_model(model_name, language='zh'):
    """Get or load a real-time transcription model"""
    key = f"realtime_{model_name}_{language}"
    # Simplified logic from original app.py
    if key not in realtime_models:
        try:
            print(f"Loading real-time model: {model_name} for language: {language}")
            if model_name == 'sensevoice':
                 # SenseVoice Logic (Simplified for brevity, assumes transformers or FunASR)
                 model_id = "FunAudioLLM/SenseVoiceSmall"
                 if FUNASR_AVAILABLE:
                      realtime_models[key] = FunASRAutoModel(model="iic/SenseVoiceSmall", device=DEVICE, disable_update=True, hub="ms")
                      realtime_models[f"{key}_type"] = "funasr"
                 else:
                      realtime_models[key] = AutoModel.from_pretrained(model_id, trust_remote_code=True, device_map=DEVICE)
                      realtime_models[f"{key}_type"] = "transformers"
            elif FASTER_AVAILABLE:
                device = "cuda" if torch.cuda.is_available() else "cpu"
                compute_type = "float16" if device == "cuda" else "int8"
                realtime_models[key] = FasterWhisperModel(model_name, device=device, compute_type=compute_type)
            else:
                realtime_models[key] = whisper.load_model(model_name, device=DEVICE)
                
            print(f"Real-time model loaded: {key}")
        except Exception as e:
            print(f"Failed to load real-time model {key}: {e}")
            return None
    return realtime_models[key]

def process_realtime_audio(sid, audio_queue, model, language, model_name):
    # Logic copied from original app.py
    buffer = []
    buffer_duration = 0
    min_chunk_duration = 3.0 

    try:
        while True:
            try:
                audio_chunk = audio_queue.get(timeout=1.0)
                if audio_chunk is None: break
            except queue.Empty:
                continue

            if isinstance(audio_chunk, np.ndarray):
                audio_data = audio_chunk.astype(np.float32, copy=False)
            else:
                audio_data = np.frombuffer(audio_chunk, dtype=np.int16).astype(np.float32) / 32768.0

            buffer.extend(audio_data.tolist())
            buffer_duration += len(audio_data) / 16000.0

            if buffer_duration >= min_chunk_duration:
                buffer_array = np.array(buffer, dtype=np.float32)
                
                # Transcribe
                transcription = ""
                # ... (Assuming simplified transcription logic for brevity here)
                # In full implementation, reuse the robust logic from original app.py
                try:
                     if FASTER_AVAILABLE and isinstance(model, FasterWhisperModel):
                         segments, _ = model.transcribe(buffer_array, language=language, beam_size=5)
                         transcription = " ".join([s.text for s in segments])
                     else:
                         result = model.transcribe(buffer_array, language=language)
                         transcription = result["text"]
                except:
                     pass

                if transcription.strip():
                     final_text = transcription.strip()
                     if OPENCC_AVAILABLE and language == 'zh':
                         final_text = converter.convert(final_text)
                     socketio.emit('transcription', {'text': final_text, 'timestamp': int(time.time() * 1000)}, room=sid)

                buffer = []
                buffer_duration = 0

    except Exception as e:
        print(f"Real-time processing error for {sid}: {e}")
    finally:
         socketio.emit('transcription_stopped', {'status': 'stopped'}, room=sid)

# SocketIO Events
@socketio.on('connect')
def handle_connect():
    print(f"Client connected: {request.sid}")
    return {'status': 'success'}

@socketio.on('disconnect')
def handle_disconnect():
    print(f"Client disconnected: {request.sid}")
    # Cleanup logic...
    sid = request.sid
    if sid in realtime_audio_queues:
        realtime_audio_queues[sid].put(None)
    
@socketio.on('start_transcription')
def handle_start(data):
    sid = request.sid
    model_name = data.get('model', 'base')
    language = data.get('language', 'zh')
    
    model = get_realtime_model(model_name, language)
    if not model:
         socketio.emit('error', {'message': 'Model failed'}, room=sid)
         return
         
    realtime_audio_queues[sid] = queue.Queue()
    t = threading.Thread(target=process_realtime_audio, args=(sid, realtime_audio_queues[sid], model, language, model_name))
    t.start()
    realtime_threads[sid] = t
    socketio.emit('transcription_started', {'status': 'success'}, room=sid)

@socketio.on('audio_chunk')
def handle_chunk(data):
    sid = request.sid
    if sid in realtime_audio_queues:
         chunk = data.get('audio')
         if chunk:
              audio_bytes = base64.b64decode(chunk)
              realtime_audio_queues[sid].put(audio_bytes)

@socketio.on('stop_transcription')
def handle_stop(data=None):
    sid = request.sid
    if sid in realtime_audio_queues:
        realtime_audio_queues[sid].put(None)
    socketio.emit('transcription_stopped', {'status': 'success'}, room=sid)

# Only keeping the realtime page route
@app.route('/')
def index():
    return send_file('realtime.html')

@app.route('/realtime.html')
def realtime_page():
    return send_file('realtime.html')

if __name__ == '__main__':
    print("🚀 Starting Real-time Transcription Server...")
    socketio.run(app, debug=True, port=5001, host='0.0.0.0')
