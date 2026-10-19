"""Save a web page as one self-contained HTML file with SingleFile.

The spike showed Obsidian blocks a "save complete" page's _files folder, so the
snapshot must inline its styles and images. Scripts are blocked.
"""
import subprocess
from pathlib import Path
from urllib.parse import urlparse

TIMEOUT_S = 180


class CaptureError(RuntimeError):
    pass


def check_url(url: str) -> str:
    url = url.strip()
    parts = urlparse(url)
    if parts.scheme not in ('http', 'https') or not parts.netloc:
        raise ValueError(f'not a web address: {url!r}')
    return url


def capture(url: str, out: Path, single_file: str, browser: str) -> None:
    if not single_file:
        raise CaptureError('SingleFile is not installed. Run make setup.')
    if not browser:
        raise CaptureError('No Chromium found for SingleFile. Set the browser path in the plugin settings.')
    cmd = [
        single_file, check_url(url), str(out),
        f'--browser-executable-path={browser}',
        '--browser-headless=true',
        '--block-scripts=true',
        '--filename-conflict-action=overwrite',
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise CaptureError(f'SingleFile took more than {TIMEOUT_S} s') from None
    if r.returncode != 0 or not out.exists() or out.stat().st_size == 0:
        detail = (r.stderr or r.stdout).strip().splitlines()[-1:] or ['no output']
        raise CaptureError(f'SingleFile failed: {detail[0]}')
