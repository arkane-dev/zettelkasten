"""Kernels: real ipykernel processes, using the dev environment's Python."""
import shutil
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from zk_sidecar.app import create_app
from zk_sidecar.config import Config
from zk_sidecar.kernels.env import KERNEL, VaultPython
from zk_sidecar.kernels.pool import CUT, MAX_STREAM, KernelError, KernelPool, Run

pytest.importorskip('ipykernel')


@pytest.fixture
def pool(tmp_path: Path):
    (tmp_path / 'notes').mkdir()
    p = KernelPool(VaultPython(tmp_path, python=sys.executable))
    yield p
    p.close()


def done(run: Run, timeout: float = 60) -> dict:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        state = run.wait(run.version, 1)
        if state['state'] in ('done', 'error', 'interrupted'):
            return state
    raise AssertionError(f'run still {run.state} after {timeout}s')


def text(state: dict) -> str:
    return ''.join(o.get('text', '') for o in state['outputs'])


def test_state_carries_between_runs(pool: KernelPool):
    assert done(pool.run('a.md', 'x = 1 + 1', language='python'))['state'] == 'done'
    state = done(pool.run('a.md', 'print(x * 21)', language='python'))
    assert state['state'] == 'done'
    assert text(state) == '42\n'
    assert state['kernel'] == KERNEL
    assert state['execution_count'] == 2


def test_each_file_has_its_own_kernel(pool: KernelPool):
    done(pool.run('a.md', 'x = 1', language='python'))
    state = done(pool.run('b.md', 'x', language='python'))
    assert state['state'] == 'error'
    assert state['outputs'][-1]['ename'] == 'NameError'
    assert {s['path'] for s in pool.status()} == {'a.md', 'b.md'}


def test_results_and_plots_use_nbformat(pool: KernelPool):
    state = done(pool.run('a.md', '6 * 7', language='python'))
    assert state['outputs'] == [{'output_type': 'execute_result', 'data': {'text/plain': '42'},
                                 'metadata': {}, 'execution_count': 1}]
    plot = 'import matplotlib.pyplot as plt\nplt.plot([1, 2, 3])\nplt.show()'
    state = done(pool.run('a.md', plot, language='python'))
    images = [o for o in state['outputs'] if 'image/png' in o.get('data', {})]
    assert len(images) == 1 and images[0]['output_type'] == 'display_data'


def test_code_runs_from_the_note_folder(pool: KernelPool, tmp_path: Path):
    state = done(pool.run('notes/a.md', 'import os; print(os.getcwd())', language='python'))
    assert text(state).strip() == str(tmp_path / 'notes')


def test_interrupt(pool: KernelPool):
    run = pool.run('a.md', 'import time\nprint("start", flush=True)\ntime.sleep(30)', language='python')
    end = time.monotonic() + 30
    while 'start' not in text(run.public()) and time.monotonic() < end:
        run.wait(run.version, 1)
    assert pool.interrupt('a.md') == 1
    state = done(run, 10)
    assert state['state'] == 'interrupted'


def test_restart_clears_state(pool: KernelPool):
    done(pool.run('a.md', 'x = 1', language='python'))
    assert pool.restart('a.md') == 1
    state = done(pool.run('a.md', 'x', language='python'))
    assert state['outputs'][-1]['ename'] == 'NameError'


def test_a_dead_kernel_is_replaced(pool: KernelPool):
    state = done(pool.run('a.md', 'import os; os._exit(1)', language='python'))
    assert state['state'] == 'error'
    assert 'kernel stopped' in state['error']
    assert done(pool.run('a.md', 'print("back")', language='python'))['state'] == 'done'


def test_resolve(pool: KernelPool):
    assert pool.resolve(language='python') == KERNEL
    assert pool.resolve(kernel='python3') == KERNEL
    with pytest.raises(KernelError, match='No Jupyter kernel for cobol'):
        pool.resolve(language='cobol')


def msg(kind: str, **content) -> dict:
    return {'msg_type': kind, 'content': content}


def test_streams_merge_and_long_output_is_cut():
    run = Run(path='a.md', kernel=KERNEL)
    run.handle(msg('stream', name='stdout', text='a'))
    run.handle(msg('stream', name='stdout', text='b'))
    run.handle(msg('stream', name='stderr', text='c'))
    assert [o['text'] for o in run.outputs] == ['ab', 'c']
    run.handle(msg('stream', name='stderr', text='x' * (MAX_STREAM + 10)))
    assert run.outputs[-1]['text'].startswith(CUT)
    assert len(run.outputs[-1]['text']) == len(CUT) + MAX_STREAM


def test_clear_output_and_display_updates():
    run = Run(path='a.md', kernel=KERNEL)
    run.handle(msg('stream', name='stdout', text='10%'))
    run.handle(msg('clear_output', wait=True))
    assert len(run.outputs) == 1
    run.handle(msg('stream', name='stdout', text='50%'))
    assert [o['text'] for o in run.outputs] == ['50%']
    run.handle(msg('display_data', data={'text/plain': 'old'}, metadata={}, transient={'display_id': 'd'}))
    run.handle(msg('update_display_data', data={'text/plain': 'new'}, metadata={}, transient={'display_id': 'd'}))
    assert run.outputs[-1]['data'] == {'text/plain': 'new'}
    assert run.handle(msg('status', execution_state='idle')) is True
    assert run.state == 'done'


TOKEN = 'test-token'
AUTH = {'Authorization': f'Bearer {TOKEN}'}


def test_run_routes(pool: KernelPool, tmp_path: Path):
    with TestClient(create_app(Config(vault=tmp_path, token=TOKEN), pool=pool)) as client:
        assert client.post('/runs', json={'path': 'a.md', 'code': '  '}, headers=AUTH).status_code == 400
        assert client.post('/runs', json={'path': 'a.md', 'code': '1', 'language': 'cobol'},
                           headers=AUTH).status_code == 400
        assert client.get('/runs/nope', headers=AUTH).status_code == 404

        r = client.post('/runs', json={'path': 'a.md', 'code': 'print("hi")', 'language': 'python'}, headers=AUTH)
        assert r.status_code == 202
        run = r.json()
        for _ in range(60):
            run = client.get(f'/runs/{run["id"]}?since={run["version"]}&wait=1', headers=AUTH).json()
            if run['state'] == 'done':
                break
        assert run['outputs'] == [{'output_type': 'stream', 'name': 'stdout', 'text': 'hi\n'}]

        kernels = client.get('/kernels', headers=AUTH).json()
        assert kernels['kernels'][0]['name'] == KERNEL
        assert kernels['running'] == [{'path': 'a.md', 'kernel': KERNEL, 'busy': False}]
        assert client.post('/kernels/shutdown', json={'path': 'a.md'}, headers=AUTH).json() == {'kernels': 1}
        assert client.get('/kernels', headers=AUTH).json()['running'] == []


@pytest.mark.skipif(not shutil.which('uv'), reason='needs uv')
def test_vault_python_is_made_on_first_run(tmp_path: Path):
    p = KernelPool(VaultPython(tmp_path))
    try:
        assert p.env.needs_setup()
        run = p.run('a.md', 'import sys; print(sys.prefix)', language='python')
        steps = set()
        end = time.monotonic() + 300
        while run.state not in ('done', 'error') and time.monotonic() < end:
            steps.add(run.wait(run.version, 1)['step'])
        assert run.state == 'done', run.error
        assert 'setting up Python for this vault' in steps
        assert text(run.public()).strip() == str(tmp_path / '.zk' / 'python')
        assert (tmp_path / '.zk' / 'kernels' / KERNEL / 'kernel.json').exists()
    finally:
        p.close()
