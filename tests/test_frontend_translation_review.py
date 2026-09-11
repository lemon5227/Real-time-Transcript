import subprocess
from pathlib import Path


def test_export_can_render_original_bilingual_and_translated_text():
    script = r'''
const fs = require('fs');
const vm = require('vm');
const context = {window: {}};
vm.runInNewContext(fs.readFileSync('./static/export.js', 'utf8'), context);
const session = {title: 'Class', segments: [{startMs: 0, endMs: 1000, text: 'Hello', translations: {zh: {text: '你好', status: 'ready'}}}]};
const original = context.window.EchoExport.formatPlainText(session, {translationMode: 'original', targetLanguage: 'zh'});
const bilingual = context.window.EchoExport.formatPlainText(session, {translationMode: 'bilingual', targetLanguage: 'zh'});
const translated = context.window.EchoExport.formatPlainText(session, {translationMode: 'translated', targetLanguage: 'zh'});
if (!original.includes('Hello') || original.includes('你好')) process.exit(1);
if (!bilingual.includes('Hello') || !bilingual.includes('你好')) process.exit(2);
if (!translated.includes('你好') || translated.includes('Hello')) process.exit(3);
'''
    result = subprocess.run(["node", "-e", script], cwd=Path(__file__).parents[1], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout


def test_export_can_use_refined_segments_without_replacing_live_segments():
    script = r'''
const fs = require('fs');
const vm = require('vm');
const context = {window: {}};
vm.runInNewContext(fs.readFileSync('./static/export.js', 'utf8'), context);
const session = {title: 'Class', segments: [{startMs: 0, endMs: 1000, text: 'draft'}]};
const refined = [{startMs: 0, endMs: 1000, text: 'polished'}];
const text = context.window.EchoExport.formatPlainText(session, {segments: refined});
const vtt = context.window.EchoExport.formatVtt(refined);
if (!text.includes('polished') || text.includes('draft')) process.exit(1);
if (!vtt.includes('polished')) process.exit(2);
if (session.segments[0].text !== 'draft') process.exit(3);
'''
    result = subprocess.run(["node", "-e", script], cwd=Path(__file__).parents[1], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout
