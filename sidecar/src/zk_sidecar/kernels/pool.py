"""Jupyter kernels for notes and notebooks.

One kernel per file and kernel name, so state carries between blocks the way it does in a notebook.
A run starts in the background. The plugin polls it, and each poll waits up to a second for new output.
Outputs use the nbformat shape, so notebooks can store them as they are.
"""
import queue
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field

from jupyter_client.kernelspec import KernelSpecManager, NoSuchKernel
from jupyter_client.manager import KernelManager

from .env import KERNEL, EnvError, VaultPython

MAX_RUNS = 500
MAX_STREAM = 500_000
CUT = '[earlier output cut]\n'
FINISHED = ('done', 'error', 'interrupted')


class KernelError(RuntimeError):
    pass


@dataclass
class Run:
    path: str
    kernel: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    state: str = 'queued'  # queued, starting, running, done, error, interrupted
    step: str = ''
    error: str = ''
    execution_count: int | None = None
    outputs: list[dict] = field(default_factory=list)
    version: int = 0
    _clear_on_next: bool = False
    _displays: dict[str, int] = field(default_factory=dict)
    _cond: threading.Condition = field(default_factory=threading.Condition)

    def public(self) -> dict:
        return {'id': self.id, 'path': self.path, 'kernel': self.kernel, 'state': self.state, 'step': self.step,
                'error': self.error, 'execution_count': self.execution_count, 'outputs': self.outputs,
                'version': self.version}

    def wait(self, since: int, timeout: float) -> dict:
        """Return once the run has moved past `since`, or the timeout passes."""
        with self._cond:
            self._cond.wait_for(lambda: self.version > since or self.state in FINISHED, timeout)
            return self.public()

    def update(self, **changes) -> None:
        with self._cond:
            for k, v in changes.items():
                setattr(self, k, v)
            self.version += 1
            self._cond.notify_all()

    def finish(self, state: str, error: str = '') -> None:
        if self.state not in FINISHED:
            self.update(state=state, step='', error=error)

    def handle(self, msg: dict) -> bool:
        """Apply one iopub message. True once the kernel is idle again, so the run is over."""
        kind, c = msg['msg_type'], msg['content']
        with self._cond:
            if kind == 'status':
                if c.get('execution_state') != 'idle':
                    return False
                errors = [o for o in self.outputs if o['output_type'] == 'error']
                self.state = ('interrupted' if errors and errors[-1].get('ename') == 'KeyboardInterrupt'
                              else 'error' if errors else 'done')
                self.step = ''
            elif kind == 'execute_input':
                self.execution_count = c.get('execution_count')
            elif kind == 'clear_output':
                if c.get('wait'):
                    self._clear_on_next = True
                else:
                    self._clear()
            elif kind == 'update_display_data':
                i = self._displays.get(c.get('transient', {}).get('display_id', ''))
                if i is None or i >= len(self.outputs):
                    return False
                self.outputs[i] = {**self.outputs[i], 'data': c['data'], 'metadata': c.get('metadata', {})}
            elif kind in ('stream', 'display_data', 'execute_result', 'error'):
                if self._clear_on_next:
                    self._clear()
                self._add(kind, c)
            else:
                return False
            self.version += 1
            self._cond.notify_all()
            return self.state in FINISHED

    def _clear(self) -> None:
        self.outputs = []
        self._displays = {}
        self._clear_on_next = False

    def _add(self, kind: str, c: dict) -> None:
        if kind == 'stream':
            last = self.outputs[-1] if self.outputs else None
            if last and last['output_type'] == 'stream' and last['name'] == c['name']:
                last['text'] += c['text']
            else:
                last = {'output_type': 'stream', 'name': c['name'], 'text': c['text']}
                self.outputs.append(last)
            if len(last['text']) > MAX_STREAM:
                last['text'] = CUT + last['text'][-MAX_STREAM:]
        elif kind == 'error':
            self.outputs.append({'output_type': 'error', 'ename': c.get('ename', ''),
                                 'evalue': c.get('evalue', ''), 'traceback': c.get('traceback', [])})
        else:
            out = {'output_type': kind, 'data': c.get('data', {}), 'metadata': c.get('metadata', {})}
            if kind == 'execute_result':
                out['execution_count'] = c.get('execution_count')
            if display_id := c.get('transient', {}).get('display_id'):
                self._displays[display_id] = len(self.outputs)
            self.outputs.append(out)


class Session:
    """One running kernel. A reader thread hands each iopub message to the run that sent it."""

    def __init__(self, path: str, kernel: str, km: KernelManager, on_death: Callable[['Session'], None]):
        self.path, self.kernel, self.km = path, kernel, km
        self.kc = km.client()
        self.kc.start_channels()
        self.kc.wait_for_ready(timeout=60)
        self.lock = threading.Lock()
        self.runs: dict[str, Run] = {}
        self.last_used = time.monotonic()
        self.closed = False
        self._on_death = on_death
        threading.Thread(target=self._read, daemon=True, name=f'iopub {path}').start()

    def execute(self, run: Run, code: str) -> None:
        # Hold the lock until the run is registered, so the reader can't drop its first messages.
        with self.lock:
            msg_id = self.kc.execute(code, allow_stdin=False, store_history=True)
            self.runs[msg_id] = run
            self.last_used = time.monotonic()
            run.update(state='running', step='')

    @property
    def busy(self) -> bool:
        return bool(self.runs)

    def restart(self) -> None:
        with self.lock:
            self._end_runs('error', 'The kernel restarted.')
            self.km.restart_kernel(now=True)
            self.kc.wait_for_ready(timeout=60)
            self.last_used = time.monotonic()

    def shutdown(self) -> None:
        self.closed = True
        with self.lock:
            self._end_runs('error', 'The kernel stopped.')
        try:
            self.kc.stop_channels()
            self.km.shutdown_kernel(now=True)
        except Exception:  # noqa: BLE001  shutting down anyway
            pass

    def _end_runs(self, state: str, error: str) -> None:
        for run in self.runs.values():
            run.finish(state, error)
        self.runs.clear()

    def _read(self) -> None:
        while not self.closed:
            try:
                msg = self.kc.get_iopub_msg(timeout=0.5)
            except queue.Empty:
                self._drain_shell()
                with self.lock:  # a restart holds the lock, so it never looks like a death
                    dead = not self.closed and not self.km.is_alive()
                    if dead:
                        self._end_runs('error', 'The kernel stopped. Run again to start a new one.')
                if dead:
                    self._on_death(self)
                    return
                continue
            except Exception:  # noqa: BLE001  channels closed during shutdown
                if self.closed:
                    return
                raise
            parent = msg.get('parent_header', {}).get('msg_id')
            with self.lock:
                run = self.runs.get(parent)
                if run and run.handle(msg):
                    del self.runs[parent]
                    self.last_used = time.monotonic()

    def _drain_shell(self) -> None:
        """Execute replies pile up otherwise. The iopub stream already says everything they do."""
        try:
            while True:
                self.kc.get_shell_msg(timeout=0)
        except queue.Empty:
            pass
        except Exception:  # noqa: BLE001
            pass


class KernelPool:
    def __init__(self, env: VaultPython, idle_minutes: float = 60):
        self.env = env
        self.specs = KernelSpecManager()
        self.specs.kernel_dirs = [str(env.kernel_dir), *self.specs.kernel_dirs]
        self.idle_s = idle_minutes * 60
        self.sessions: dict[tuple[str, str], Session] = {}
        self.runs: OrderedDict[str, Run] = OrderedDict()
        self._lock = threading.Lock()
        self._starting: dict[tuple[str, str], threading.Lock] = {}
        self._stop = threading.Event()
        threading.Thread(target=self._cull, daemon=True, name='kernel cull').start()

    def kernels(self) -> list[dict]:
        """Kernels to pick from. The vault's Python stands in for `python3`."""
        out = [{'name': KERNEL, 'language': 'python', 'display_name': 'Python (vault)'}]
        for name, info in sorted(self.specs.get_all_specs().items()):
            if name in (KERNEL, 'python3'):
                continue
            spec = info['spec']
            out.append({'name': name, 'language': spec.get('language', ''),
                        'display_name': spec.get('display_name', name)})
        return out

    def resolve(self, kernel: str = '', language: str = '') -> str:
        """A kernel name from a name, a language, or both. Python means the vault's Python."""
        if kernel in ('', 'python3', 'python', KERNEL) and (kernel or language.lower() in ('python', 'py', 'python3')):
            return KERNEL
        names = {k['name']: k for k in self.kernels()}
        if kernel in names:
            return kernel
        lang = language.lower()
        for k in names.values():
            if lang and k['language'].lower() == lang:
                return k['name']
        raise KernelError(f'No Jupyter kernel for {kernel or language}. Install one, then restart the sidecar.')

    def run(self, path: str, code: str, kernel: str = '', language: str = '') -> Run:
        name = self.resolve(kernel, language)
        run = Run(path=path, kernel=name)
        with self._lock:
            self.runs[run.id] = run
            while len(self.runs) > MAX_RUNS:
                self.runs.popitem(last=False)
        threading.Thread(target=self._start, args=(run, code), daemon=True, name=f'run {run.id}').start()
        return run

    def get(self, run_id: str) -> Run | None:
        return self.runs.get(run_id)

    def interrupt(self, path: str, kernel: str = '') -> int:
        found = self._find(path, kernel)
        for s in found:
            s.km.interrupt_kernel()
        return len(found)

    def restart(self, path: str, kernel: str = '') -> int:
        found = self._find(path, kernel)
        for s in found:
            s.restart()
        return len(found)

    def shutdown(self, path: str | None = None, kernel: str = '') -> int:
        name = self.resolve(kernel) if kernel else ''
        with self._lock:
            found = [s for key, s in self.sessions.items()
                     if path is None or (key[0] == path and name in ('', key[1]))]
            for s in found:
                self.sessions.pop((s.path, s.kernel), None)
        for s in found:
            s.shutdown()
        return len(found)

    def close(self) -> None:
        self._stop.set()
        self.shutdown()

    def status(self) -> list[dict]:
        return [{'path': s.path, 'kernel': s.kernel, 'busy': s.busy} for s in list(self.sessions.values())]

    def _find(self, path: str, kernel: str) -> list[Session]:
        name = self.resolve(kernel) if kernel else ''
        return [s for (p, k), s in list(self.sessions.items()) if p == path and name in ('', k)]

    def _start(self, run: Run, code: str) -> None:
        try:
            session = self._session(run)
            session.execute(run, code)
        except (EnvError, KernelError, NoSuchKernel) as e:
            run.finish('error', str(e))
        except Exception as e:  # noqa: BLE001  the plugin shows the message
            run.finish('error', f'Could not run the code: {e}')

    def _session(self, run: Run) -> Session:
        key = (run.path, run.kernel)
        with self._lock:
            if (s := self.sessions.get(key)) and not s.closed:
                return s
            gate = self._starting.setdefault(key, threading.Lock())
        with gate:  # a second run for the same file waits for the first kernel instead of starting another
            with self._lock:
                if (s := self.sessions.get(key)) and not s.closed:
                    return s
            if run.kernel == KERNEL:
                self.env.ensure(lambda step: run.update(state='starting', step=step))
            run.update(state='starting', step='starting the kernel')
            km = KernelManager(kernel_name=run.kernel, kernel_spec_manager=self.specs)
            km.start_kernel(cwd=self._cwd(run.path))
            try:
                s = Session(run.path, run.kernel, km, self._forget)
            except Exception:
                km.shutdown_kernel(now=True)
                raise
            with self._lock:
                self.sessions[key] = s
            return s

    def _cwd(self, path: str) -> str:
        """Run code from the file's folder, so relative paths in it work."""
        vault = self.env.venv.parent.parent
        folder = (vault / path).parent
        return str(folder if folder.is_dir() and folder.resolve().is_relative_to(vault.resolve()) else vault)

    def _forget(self, session: Session) -> None:
        with self._lock:
            if self.sessions.get((session.path, session.kernel)) is session:
                del self.sessions[(session.path, session.kernel)]

    def _cull(self) -> None:
        while not self._stop.wait(60):
            now = time.monotonic()
            with self._lock:
                idle = [s for s in self.sessions.values() if not s.busy and now - s.last_used > self.idle_s]
                for s in idle:
                    del self.sessions[(s.path, s.kernel)]
            for s in idle:
                s.shutdown()
