"""The vault's own Python, for code in notes and notebooks.

It lives in `.zk/python`. uv makes it on the first run, with ipykernel and pip, so `%pip install` works.
A kernel spec for it goes in `.zk/kernels/zk-python`. A Python set in the settings replaces it.
"""
import json
import os
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path

KERNEL = 'zk-python'
SETUP_TIMEOUT = 600


class EnvError(RuntimeError):
    pass


def find_uv() -> str:
    for path in (shutil.which('uv'), '~/.local/bin/uv', '~/.cargo/bin/uv'):
        if path and Path(path).expanduser().is_file():
            return str(Path(path).expanduser())
    return ''


def venv_python(venv: Path) -> Path:
    return venv / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


class VaultPython:
    def __init__(self, vault: Path, python: str = '', uv: str | None = None):
        self.venv = vault / '.zk' / 'python'
        self.kernel_dir = vault / '.zk' / 'kernels'
        self.override = python
        self.uv = find_uv() if uv is None else uv
        self._lock = threading.Lock()
        self._checked = False

    @property
    def python(self) -> Path:
        return Path(self.override) if self.override else venv_python(self.venv)

    def needs_setup(self) -> bool:
        return not self.override and not self.python.exists()

    def ensure(self, step: Callable[[str], None] = lambda s: None) -> None:
        """Make the environment if it's missing, and write its kernel spec."""
        with self._lock:
            if self.needs_setup():
                step('setting up Python for this vault')
                self._create()
            elif not self._checked:
                self._check()
            self._checked = True
            self._write_spec()

    def _create(self) -> None:
        target = str(self.python)
        if self.uv:
            self._sh([self.uv, 'venv', '--seed', '--quiet', str(self.venv)])
            self._sh([self.uv, 'pip', 'install', '--quiet', '--python', target, 'ipykernel'])
        else:
            self._sh([sys.executable, '-m', 'venv', str(self.venv)])
            self._sh([target, '-m', 'pip', 'install', '--quiet', 'ipykernel'])

    def _check(self) -> None:
        if not self.python.exists():
            raise EnvError(f'Python not found: {self.python}')
        r = subprocess.run([str(self.python), '-c', 'import ipykernel'], capture_output=True, timeout=60)
        if r.returncode:
            raise EnvError(f'{self.python} has no ipykernel. Install it with: {self.python} -m pip install ipykernel')

    def _write_spec(self) -> None:
        spec = {
            'argv': [str(self.python), '-m', 'ipykernel_launcher', '-f', '{connection_file}'],
            'display_name': 'Python (vault)' if not self.override else f'Python ({self.python})',
            'language': 'python',
            'metadata': {'debugger': False},
        }
        folder = self.kernel_dir / KERNEL
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'kernel.json').write_text(json.dumps(spec, indent=1))

    @staticmethod
    def _sh(cmd: list[str]) -> None:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=SETUP_TIMEOUT)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise EnvError(f'Python setup failed: {e}') from None
        if r.returncode:
            tail = '\n'.join((r.stderr or r.stdout).strip().splitlines()[-8:])
            raise EnvError(f'Python setup failed: {" ".join(cmd[:3])}\n{tail}')
