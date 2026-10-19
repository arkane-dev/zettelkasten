"""Run the sidecar as the plugin does: a child process, a token in the environment, a ready line on stdout."""
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest

TOKEN = 'process-token'


@pytest.fixture
def sidecar(tmp_path: Path):
    env = {**os.environ, 'ZK_TOKEN': TOKEN}
    proc = subprocess.Popen(
        [sys.executable, '-m', 'zk_sidecar', '--vault', str(tmp_path), '--watch-stdin'],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, text=True)
    line = proc.stdout.readline()
    assert line.startswith('ZK_READY '), proc.stderr.read()
    yield proc, json.loads(line.split(' ', 1)[1])
    if proc.poll() is None:
        proc.kill()


def get(port: int, path: str) -> dict:
    req = urllib.request.Request(f'http://127.0.0.1:{port}{path}', headers={'Authorization': f'Bearer {TOKEN}'})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.load(r)


def test_ready_line_gives_a_working_port(sidecar):
    _, ready = sidecar
    assert ready['port'] > 0
    assert get(ready['port'], '/health')['ok'] is True


def test_exits_when_stdin_closes(sidecar):
    proc, _ = sidecar
    proc.stdin.close()
    assert proc.wait(timeout=10) == 0


def test_refuses_to_start_without_a_token(tmp_path: Path):
    env = {k: v for k, v in os.environ.items() if k != 'ZK_TOKEN'}
    r = subprocess.run([sys.executable, '-m', 'zk_sidecar', '--vault', str(tmp_path)],
                       capture_output=True, text=True, env=env, timeout=30)
    assert r.returncode != 0
    assert 'ZK_TOKEN' in r.stderr
