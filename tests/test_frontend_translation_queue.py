import subprocess
from pathlib import Path


def test_translation_queue_filters_final_segments_batches_and_serializes_requests():
    script = r'''
const { createTranslationQueue } = require('./static/translation-queue.js');
const sent = [];
let active = 0;
let maxActive = 0;
const queue = createTranslationQueue({
  batchSize: 3,
  maxChars: 100,
  send: async (items) => {
    active += 1;
    maxActive = Math.max(maxActive, active);
    sent.push(items.map(item => item.id));
    await new Promise(resolve => setTimeout(resolve, 5));
    active -= 1;
    return { status: 'success', translations: items.map(item => ({ segment_id: item.id, text: 'T:' + item.text })) };
  }
});
queue.enqueue({ id: 'provisional', text: 'ignore', is_final: false });
queue.enqueue({ id: '1', text: 'one', is_final: true });
queue.enqueue({ id: '2', text: 'two', is_final: true });
queue.enqueue({ id: '1', text: 'one duplicate', is_final: true });
queue.enqueue({ id: '3', text: 'three', is_final: true });
queue.enqueue({ id: '4', text: 'four', is_final: true });
await queue.flush();
if (JSON.stringify(sent) !== JSON.stringify([['1', '2', '3'], ['4']])) process.exit(1);
if (maxActive !== 1) process.exit(2);
if (queue.getState().pending !== 0) process.exit(3);
'''
    result = subprocess.run(["node", "-e", "(async()=>{" + script + "})()"], cwd=Path(__file__).parents[1], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout


def test_translation_queue_stop_flushes_pending_once_and_retry_can_requeue():
    script = r'''
const { createTranslationQueue } = require('./static/translation-queue.js');
let calls = 0;
const queue = createTranslationQueue({
  batchSize: 5,
  send: async items => { calls += 1; return { status: 'success', translations: items.map(item => ({ segment_id: item.id, text: item.text })) }; }
});
queue.enqueue({ id: '1', text: 'one', is_final: true });
await queue.stop();
await queue.stop();
if (calls !== 1) process.exit(1);
queue.start();
queue.enqueue({ id: '2', text: 'two', is_final: true });
await queue.flush();
if (calls !== 2) process.exit(2);
'''
    result = subprocess.run(["node", "-e", "(async()=>{" + script + "})()"], cwd=Path(__file__).parents[1], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout


def test_browser_global_exposes_the_factory_name_used_by_live_app():
    script = r'''
const fs = require('fs');
const vm = require('vm');
const context = {window: {}};
vm.runInNewContext(fs.readFileSync('./static/translation-queue.js', 'utf8'), context);
if (!context.window.EchoTranslationQueue || typeof context.window.EchoTranslationQueue.create !== 'function') process.exit(1);
'''
    result = subprocess.run(["node", "-e", script], cwd=Path(__file__).parents[1], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout


def test_final_fragment_does_not_shrink_a_longer_live_sentence():
    script = r'''
const { resolve, remainder } = require('./static/transcript-current.js');
const live = { text: 'When you run your notebooks more times with more and more rows here', start_ms: 1000, end_ms: 8000, is_final: false };
const final = { text: 'When you run your notebooks', start_ms: 1000, end_ms: 3500, is_final: true };
const result = resolve(live.text, final, live);
if (result.text !== live.text || result.provisional !== true) process.exit(1);
if (remainder(final.text, live.text) !== 'more times with more and more rows here') process.exit(2);
'''
    result = subprocess.run(["node", "-e", script], cwd=Path(__file__).parents[1], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout


def test_live_sentence_accepts_a_newer_draft_and_exact_final_confirmation():
    script = r'''
const { resolve } = require('./static/transcript-current.js');
const first = resolve('', { text: 'The model is fast', start_ms: 1000, end_ms: 3000, is_final: false }, null);
const revised = resolve(first.text, { text: 'The model is much faster', start_ms: 1000, end_ms: 4000, is_final: false }, null);
const confirmed = resolve(revised.text, { text: revised.text, start_ms: 1000, end_ms: 4000, is_final: true }, { ...revised, start_ms: 1000, end_ms: 4000, is_final: false });
if (revised.text !== 'The model is much faster' || revised.provisional !== true) process.exit(1);
if (confirmed.text !== revised.text || confirmed.provisional !== false) process.exit(2);
'''
    result = subprocess.run(["node", "-e", script], cwd=Path(__file__).parents[1], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout
