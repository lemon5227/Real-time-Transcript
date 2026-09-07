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
