from pathlib import Path

from backend import create_app

ROOT = Path(__file__).resolve().parents[1]


def test_live_page_exposes_accessible_lecture_workbench():
    app = create_app({})
    response = app.test_client().get("/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'data-testid="live-workbench"' in html
    assert 'id="start-listening"' in html
    assert 'aria-live="polite"' in html
    assert "/static/app.js" in html


def test_live_and_review_pages_use_the_shiju_product_brand():
    app = create_app({})
    live_html = app.test_client().get("/").get_data(as_text=True)
    review_html = app.test_client().get("/review").get_data(as_text=True)

    for html in [live_html, review_html]:
        assert "拾句" in html
        assert "EchoNote" not in html
        assert "lecture, made legible" not in html


def test_live_page_keeps_topbar_focused_without_redundant_privacy_badge():
    app = create_app({})
    html = app.test_client().get("/").get_data(as_text=True)
    assert 'class="privacy-badge"' not in html


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
    assert "distil-small.en" in html
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
    assert "local_model" in app.test_client().get("/api/capabilities").get_data(as_text=True)
    assert "audio_backpressure" in javascript
    assert "本地处理较慢" in javascript
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


def test_review_page_exposes_after_class_translation_and_export_modes():
    app = create_app({})
    html = app.test_client().get("/review").get_data(as_text=True)
    javascript = app.test_client().get("/static/review.js").get_data(as_text=True)
    export = app.test_client().get("/static/export.js").get_data(as_text=True)
    for hook in ["translate-session", "translate-selected", "翻译整节课", "review-translation-status", "export-translation-mode"]:
        assert hook in html
    for hook in ["/api/translate", "translationBusy", "translate-segment", "翻译失败"]:
        assert hook in javascript
    for hook in ["translationMode", "bilingual", "translated"]:
        assert hook in export


def test_review_page_exposes_fine_transcription_controls():
    app = create_app({})
    html = app.test_client().get("/review").get_data(as_text=True)
    javascript = app.test_client().get("/static/review.js").get_data(as_text=True)
    for hook in ["refine-transcription", "refine-transcription-status", "transcript-mode-realtime", "transcript-mode-refined"]:
        assert hook in html
    for hook in ["refinedSegments", "/api/refine-transcription", "transcriptionMode"]:
        assert hook in javascript


def test_frontend_has_actionable_startup_and_recovery_copy():
    javascript = create_app({}).test_client().get("/static/app.js").get_data(as_text=True)
    assert "正在准备麦克风" in javascript
    assert "模型会在后台启动" in javascript
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


def test_live_workbench_has_planned_control_groups_and_follow_feedback():
    app = create_app({})
    html = app.test_client().get("/").get_data(as_text=True)
    javascript = app.test_client().get("/static/app.js").get_data(as_text=True)
    stylesheet = app.test_client().get("/static/styles.css").get_data(as_text=True)

    for hook in ["setup-path-field", "setup-field-grid", "setup-translation-field", "autoscroll-state", "model-selection-summary", "model-speed", "model-quality"]:
        assert hook in html
    for hook in ["updateFollowUi", "is-auto-following", "is-pulsing"]:
        assert hook in javascript
    for hook in ["follow-pulse", "return-latest-in", "prefers-reduced-motion"]:
        assert hook in stylesheet


def test_live_workbench_keeps_advanced_settings_in_a_drawer():
    app = create_app({})
    html = app.test_client().get("/").get_data(as_text=True)
    javascript = app.test_client().get("/static/app.js").get_data(as_text=True)
    stylesheet = app.test_client().get("/static/styles.css").get_data(as_text=True)

    for hook in ["settings-drawer", "settings-content", "model-management", "close-settings"]:
        assert hook in html
    assert "settings-button" not in html
    for hook in ["setupSettingsDrawer", "settings-drawer", "model-management"]:
        assert hook in javascript or hook in stylesheet
    assert "height: 100dvh" in stylesheet
    assert "overflow: hidden" in stylesheet
    assert ".actionable-error[hidden]" in stylesheet


def test_settings_drawer_has_a_cohesive_control_room_and_model_library():
    app = create_app({})
    html = app.test_client().get("/").get_data(as_text=True)
    javascript = app.test_client().get("/static/app.js").get_data(as_text=True)
    stylesheet = app.test_client().get("/static/styles.css").get_data(as_text=True)

    for hook in [
        "settings-nav",
        "settings-overview",
        "settings-section",
        "model-library-hero",
        "settings-footer",
    ]:
        assert hook in html
    for hook in ["setupSettingsNavigation", "settings-nav", "model-library-hero", "model-manager-progress"]:
        assert hook in javascript or hook in stylesheet
    for hook in ["settings-layout", "settings-nav", "settings-content-scroll", "model-library-hero", "model-manager-progress", "settings-footer"]:
        assert hook in stylesheet


def test_live_workbench_keeps_frequent_controls_in_a_right_rail():
    app = create_app({})
    html = app.test_client().get("/").get_data(as_text=True)
    javascript = app.test_client().get("/static/app.js").get_data(as_text=True)
    stylesheet = app.test_client().get("/static/styles.css").get_data(as_text=True)

    for hook in ["quick-settings", "quick-translation-toggle", "quick-path-field", "quick-rail-toggle", "quick-rail-collapsed-light", "quick-rail-panel-icon", "quick-rail-review", "data-mode=\"auto\"", "data-mode=\"local\"", "data-mode=\"cloud\"", "topbar-listen-control", "start-listening"]:
        assert hook in html
    for hook in ["setupQuickSettings", "syncQuickSettings", "setupQuickRail", "querySelectorAll(\".quick-rail-status-light\")", "quick-settings-collapsed", "quick-translation-toggle", "if (openButton) openButton.addEventListener", "if (closeButton) closeButton.addEventListener", "if (modelManagementList) modelManagementList.addEventListener"]:
        assert hook in javascript
    for hook in ["quick-model-select", "quick-language-select", "quick-microphone-select", "quick-save-audio", "quick-test-microphone"]:
        assert hook not in html
    assert "quick-ready-banner" not in html
    assert "grid-template-columns: minmax(0, 1fr) minmax(300px, 340px)" in stylesheet
    assert "topbar-listen-control" in stylesheet
    assert "quick-settings-collapsed" in stylesheet
    assert "quick-rail-collapsed-light" in stylesheet
    assert "quick-rail-panel-icon" in stylesheet
    assert "body.quick-settings-collapsed .quick-card-minimal { color: var(--ink); background: var(--paper);" in stylesheet
    assert "body.quick-settings-collapsed .quick-rail-toggle { width: 34px; height: 34px; color: var(--ink); background: transparent; border: 0; border-radius: 0;" in stylesheet
    assert "position: sticky" in stylesheet
    assert ".control-dock" not in html


def test_live_workbench_has_scroll_aware_lecture_focus_mode():
    app = create_app({})
    html = app.test_client().get("/").get_data(as_text=True)
    javascript = app.test_client().get("/static/app.js").get_data(as_text=True)
    stylesheet = app.test_client().get("/static/styles.css").get_data(as_text=True)

    for hook in ["toggle-lecture-intro", "lecture-intro-toggle-label"]:
        assert hook in html
    for hook in ["setupLectureFocus", "lecture-intro-collapsed", "echonote:recording-started", "focusForRecording", "addEventListener(\"scroll\""]:
        assert hook in javascript
    for hook in ["is-header-collapsed", "lecture-intro-compact"]:
        assert hook in stylesheet


def test_live_workbench_preserves_follow_mode_and_translates_existing_segments():
    javascript = create_app({}).test_client().get("/static/app.js").get_data(as_text=True)

    for hook in ["followScrollLock", "scrollToLatest", "enqueueExistingTranslations"]:
        assert hook in javascript
    assert "if (state.recording)" in javascript
    assert "已开启 · 下一句字幕会自动翻译" in javascript



def test_live_workbench_stamps_audio_chunks_with_the_recording_clock():
    javascript = create_app({}).test_client().get("/static/app.js").get_data(as_text=True)

    # Every audio chunk carries its capture position so the backend can keep
    # captions aligned with the saved recording.
    for hook in [
        "captureStartedAtMs",
        "captureOffsetMs",
        "payload.offset_ms",
        "startedAtMs: state.captureStartedAtMs",
    ]:
        assert hook in javascript
    assert "state.socket.emit(\"audio_chunk\", payload)" in javascript


def test_live_workbench_exposes_a_course_glossary():
    client = create_app({}).test_client()
    html = client.get("/").get_data(as_text=True)
    javascript = client.get("/static/app.js").get_data(as_text=True)

    assert 'id="glossary-input"' in html
    assert "课程词汇" in html
    for hook in ["glossary-input", "parseGlossaryInput", "payload.glossary", "glossary: $(\"#glossary-input\")"]:
        assert hook in javascript


def test_live_workbench_follow_does_not_pause_on_programmatic_scroll():
    javascript = create_app({}).test_client().get("/static/app.js").get_data(as_text=True)

    for hook in [
        "pauseFollowForUserIntent",
        'feed.addEventListener("wheel"',
        'feed.addEventListener("touchstart"',
        'feed.addEventListener("keydown"',
        'behavior: "auto"',
    ]:
        assert hook in javascript
    assert "toggle.checked = false" in javascript
    assert "if (!atBottom && toggle.checked)" not in javascript


def test_live_workbench_follow_ignores_programmatic_user_intent_events():
    javascript = create_app({}).test_client().get("/static/app.js").get_data(as_text=True)

    assert "if (state.followScrollLock || (event && event.isTrusted === false) || !toggle.checked) return;" in javascript
    assert "pauseFollowForUserIntent(event)" in javascript


def test_latency_probe_reports_streaming_chunk_separately_from_window():
    probe = (ROOT / "tools/measure_caption_latency.py").read_text(encoding="utf-8")

    assert 'streaming_chunk_seconds = capabilities["audio"]["streaming_chunk_seconds"]' in probe
    assert "streaming_chunk_seconds" in probe


def test_live_workbench_renders_one_updating_live_row_in_history_feed():
    app = create_app({})
    javascript = app.test_client().get("/static/app.js").get_data(as_text=True)
    stylesheet = app.test_client().get("/static/styles.css").get_data(as_text=True)

    for hook in ["liveSegment", "renderLiveSegment", "sameLiveSegment", "正在识别 · 会自动更新"]:
        assert hook in javascript
    assert "is-live-segment" in stylesheet


def test_live_workbench_captures_while_model_is_starting():
    javascript = create_app({}).test_client().get("/static/app.js").get_data(as_text=True)
    for hook in [
        "transcription_session_created",
        "transcription_ready",
        "正在收音 · 模型准备中",
        "pendingAudio",
        "MAX_PENDING_AUDIO_SECONDS",
        "flushPendingAudio",
    ]:
        assert hook in javascript
    send_audio_body = javascript.split("function sendAudioBuffer", 1)[1].split("function flushPendingAudio", 1)[0]
    assert "if (!state.recording" not in send_audio_body


def test_live_workbench_makes_quick_status_visible_without_a_translation_card():
    app = create_app({})
    html = app.test_client().get("/").get_data(as_text=True)
    stylesheet = app.test_client().get("/static/styles.css").get_data(as_text=True)
    javascript = app.test_client().get("/static/app.js").get_data(as_text=True)

    for hook in ["quick-translation-row", "quick-translation-copy", "quick-translation-detail", "quick-rail-status-light", "quick-rail-status-list", "quick-status-item", "quick-status-value", "quick-rail-session-state"]:
        assert hook in html
    assert "quick-translation-field" not in html
    for hook in ["quick-rail-status-light", "data-status"]:
        assert hook in javascript or hook in html
    for hook in ["quick-translation-row", "quick-rail-status-light", "quick-rail-status-list", "quick-status-item", "quick-status-value", "quick-rail-status-list dd", "--data-sans"]:
        assert hook in stylesheet


def test_startup_connection_failure_is_not_a_pre_recording_error():
    javascript = create_app({}).test_client().get("/static/app.js").get_data(as_text=True)
    assert 'showError("实时连接脚本未加载"' not in javascript
    assert "实时连接问题会显示在课前检查" in javascript


def test_mac_runtime_install_and_ui_contracts():
    requirements = (ROOT / "requirements-mac.txt").read_text(encoding="utf-8")
    assert "requirements-core.txt" in requirements
    assert "parakeet-mlx" in requirements

    app = create_app({})
    html = app.test_client().get("/").get_data(as_text=True)
    javascript = app.test_client().get("/static/app.js").get_data(as_text=True)
    assert "parakeet-tdt-0.6b-v3" in html
    assert "Mac MLX" in html
    for hook in ["recommended_model", "local.runtime", "model.runtime", "运行时"]:
        assert hook in javascript or hook in html
