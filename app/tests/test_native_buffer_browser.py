"""A real browser reuses source bytes but cannot bypass a stale-file check."""
import json
from pathlib import Path
import os
import subprocess
import threading

import pytest

from moviestar.local_preview import PreviewServer, PreviewSession
from moviestar.spatial_layout import browser_available
from tests.test_live_browser_preview import live_project
from tests.test_native_preview import loaded_native_project, _fake_workers, _complete


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
def test_browser_hot_source_bytes_still_validate_changed_file(loaded_native_project, tmp_path, monkeypatch):
    _, _, root = loaded_native_project
    folder = tmp_path / 'packets'; folder.mkdir()
    with monkeypatch.context() as fake:
        _fake_workers(fake)
        session = PreviewSession(root, 0, 5, folder, mode='native'); _complete(session)
    server = PreviewServer(session.queue, token='a' * 48, port=0, mode='native', session=session)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    helper = (Path(__file__).parents[2] / 'experiments/live_browser_preview/browser.mjs').as_uri()
    chrome = os.environ.get('MOVIESTAR_CHROME_PATH')
    if not chrome:
        from moviestar.spatial_layout import _chrome_path
        chrome = _chrome_path()
    script = r'''
import {readFile,appendFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
const {Browser}=await import(HELPER),browser=await Browser.launch(CHROME,OUT);
try{
 await browser.send('Page.navigate',{url:URL});
 await browser.wait('window.movieStarPreview?.is_current&&navigator.serviceWorker.controller',15000);
 assert.equal(await browser.evaluate('window.movieStarPreview.stage.metrics.source_buffering'),true);
 const source=await browser.evaluate("document.querySelector('iframe').contentDocument.querySelector('video').src.split('#')[0]");
 const state=async()=>(await(await fetch(URL+'state')).json()).native_video_delivery;
 const before=await state();
 const hot=await browser.evaluate(`fetch(${JSON.stringify(source)},{headers:{Range:'bytes=0-5'}}).then(async r=>({status:r.status,bytes:[...new Uint8Array(await r.arrayBuffer())]}))`);
 assert.equal(hot.status,206);
 assert.deepEqual(hot.bytes,[...(await readFile(SOURCE)).subarray(0,6)]);
 const after=await state();
 assert.equal(after.bytes_written,before.bytes_written,'A fully hot range transfers no source body');
 assert.ok(after.validated_heads>before.validated_heads,'The server must still validate a hot request');
 // Append a valid MP4 free box to change identity without changing decode
 // compatibility. The old asset must now fail even though its bytes are hot.
 await appendFile(SOURCE,Buffer.from([0,0,0,8,102,114,101,101]));
 const stale=await browser.evaluate(`fetch(${JSON.stringify(source)},{headers:{Range:'bytes=0-5'}}).then(r=>r.status)`);
 assert.equal(stale,409,'Cached bytes cannot bypass source identity');
 assert.equal((await state()).bytes_written,after.bytes_written);
}finally{await browser.close();}
'''
    values = {'HELPER': helper, 'CHROME': str(chrome), 'OUT': str(tmp_path / 'browser'),
              'URL': server.url, 'SOURCE': str(root / 'camera.mp4')}
    prefix = ''.join('const ' + key + '=' + json.dumps(value) + ';\n' for key, value in values.items())
    try:
        result = subprocess.run(['node', '--input-type=module', '-e', prefix + script],
                                capture_output=True, text=True, timeout=40)
        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        server.shutdown(); server.server_close(); thread.join()
