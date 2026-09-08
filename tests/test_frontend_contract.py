from backend import create_app


def test_live_page_exposes_accessible_lecture_workbench():
    app = create_app({})
    response = app.test_client().get("/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'data-testid="live-workbench"' in html
    assert 'id="start-listening"' in html
    assert 'aria-live="polite"' in html
    assert "/static/app.js" in html


def test_frontend_assets_contain_live_audio_and_review_hooks():
    app = create_app({})
    javascript = app.test_client().get("/static/app.js").get_data(as_text=True)
    stylesheet = app.test_client().get("/static/styles.css").get_data(as_text=True)
    assert "audio_chunk" in javascript
    assert "indexedDB" in javascript
    assert "prefers-reduced-motion" in stylesheet


def test_live_page_exposes_class_readiness_controls():
    app = create_app({})
    html = app.test_client().get("/").get_data(as_text=True)
    for hook in ["readiness-panel", "model-download-button", "microphone-select", "mic-level"]:
        assert hook in html
    javascript = app.test_client().get("/static/app.js").get_data(as_text=True)
    assert "/api/models" in javascript
    assert "enumerateDevices" in javascript
    assert "preparing" in javascript


def test_frontend_requests_selected_microphone_and_has_signal_health():
    javascript = create_app({}).test_client().get("/static/app.js").get_data(as_text=True)
    assert "deviceId" in javascript
    assert "createAnalyser" in javascript
    assert "没有检测到麦克风声音" in javascript


def test_live_page_loads_local_audio_recording_dependencies():
    app = create_app({})
    html = app.test_client().get("/").get_data(as_text=True)
    assert "/static/audio-storage.js" in html
    assert "/static/audio-recorder.js" in html
    storage = app.test_client().get("/static/audio-storage.js").get_data(as_text=True)
    recorder = app.test_client().get("/static/audio-recorder.js").get_data(as_text=True)
    assert "EchoAudioRepository" in storage
    assert "MediaRecorder" in recorder


def test_live_page_exposes_default_local_audio_save_controls():
    app = create_app({})
    html = app.test_client().get("/").get_data(as_text=True)
    javascript = app.test_client().get("/static/app.js").get_data(as_text=True)
    for hook in ["save-audio", "audio-readiness-status", "audio-storage-status"]:
        assert hook in html
    for hook in ["saveAudio: true", "EchoAudioRecorder", "正在保存原声", "仅保存字幕"]:
        assert hook in javascript
    storage = app.test_client().get("/static/audio-storage.js").get_data(as_text=True)
    assert "createRecording" in storage


def test_live_page_exposes_translation_controls_and_queue():
    app = create_app({})
    html = app.test_client().get("/").get_data(as_text=True)
    javascript = app.test_client().get("/static/app.js").get_data(as_text=True)
    queue = app.test_client().get("/static/translation-queue.js").get_data(as_text=True)
    for hook in ["translation-mode", "translation-provider", "translation-target", "translation-status"]:
        assert hook in html
    for hook in ["EchoTranslationQueue", "translation_result", "实时翻译", "翻译失败"]:
        assert hook in javascript
    assert "batchSize" in queue


def test_review_page_exposes_audio_playback_hooks():
    app = create_app({})
    html = app.test_client().get("/review").get_data(as_text=True)
    javascript = app.test_client().get("/static/review.js").get_data(as_text=True)
    for hook in ["reviewAudio", "reviewAudioStatus", "export-audio"]:
        assert hook in html
    for hook in ["getPlayableBlob", "currentTime", "timeupdate", "revokeObjectURL"]:
        assert hook in javascript


def test_translation_queue_is_loaded_by_live_page():
    html = create_app({}).test_client().get("/").get_data(as_text=True)
    assert "/static/translation-queue.js" in html


def test_frontend_has_actionable_startup_and_recovery_copy():
    javascript = create_app({}).test_client().get("/static/app.js").get_data(as_text=True)
    assert "正在准备麦克风" in javascript
    assert "正在加载模型" in javascript
    assert "重试麦克风" in javascript
    assert "切换到云端" in javascript
    assert 'phase !== "stopping"' in javascript


def test_pages_include_keyboard_and_mobile_safety_hooks():
    app = create_app({})
    live = app.test_client().get("/").get_data(as_text=True)
    review = app.test_client().get("/review").get_data(as_text=True)
    stylesheet = app.test_client().get("/static/styles.css").get_data(as_text=True)
    assert 'class="skip-link"' in live
    assert 'class="skip-link"' in review
    assert "safe-area-inset-bottom" in stylesheet
    assert 'aria-label="搜索课堂笔记"' in review
