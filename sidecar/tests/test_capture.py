"""SingleFile against a local page with a stylesheet and an image. Skipped if SingleFile or Chromium is missing."""
import base64
import functools
import http.server
import threading
from pathlib import Path

import pytest

from zk_sidecar.config import find_browser, find_single_file
from zk_sidecar.sources.capture import CaptureError, capture

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==')

pytestmark = pytest.mark.skipif(not (find_single_file() and find_browser()), reason='needs SingleFile and Chromium')


@pytest.fixture
def site(tmp_path: Path):
    root = tmp_path / 'site'
    root.mkdir()
    (root / 'index.html').write_text(
        '<html><head><title>Local page</title><link rel="stylesheet" href="style.css"></head>'
        '<body><h1>Hello</h1><img src="dot.png" alt="dot"><script>document.title = "changed"</script></body></html>')
    (root / 'style.css').write_text('h1 { color: rgb(1, 2, 3); }')
    (root / 'dot.png').write_bytes(PNG)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f'http://127.0.0.1:{server.server_address[1]}/index.html'
    server.shutdown()


def test_capture_inlines_styles_and_images(site: str, tmp_path: Path):
    out = tmp_path / 'snap.html'
    capture(site, out, find_single_file(), find_browser())
    html = out.read_text()
    assert 'color:rgb(1,2,3)' in html.replace(' ', '')
    assert 'data:image/png;base64' in html
    assert 'style.css' not in html
    assert 'document.title' not in html


def test_capture_reports_a_dead_host(tmp_path: Path):
    with pytest.raises(CaptureError):
        capture('http://127.0.0.1:9/', tmp_path / 'x.html', find_single_file(), find_browser())
