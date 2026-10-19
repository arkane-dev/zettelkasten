"""Background jobs. One worker, so LLM calls never compete for the GPU."""
import queue
import threading
import uuid
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import asdict, dataclass

KEEP = 200


@dataclass
class Job:
    id: str
    action: str
    state: str = 'queued'  # queued, working, done, error
    step: str = ''  # what it's doing now: capturing, converting, summarising
    note: str | None = None  # vault-relative path, set as soon as the note exists
    duplicate: bool = False
    unchanged: bool = False
    warning: str = ''
    error: str = ''

    def public(self) -> dict:
        return asdict(self)


class Jobs:
    def __init__(self):
        self._jobs: OrderedDict[str, Job] = OrderedDict()
        self._queue: queue.Queue[tuple[Job, Callable[[Job], None]]] = queue.Queue()
        self._lock = threading.Lock()
        threading.Thread(target=self._work, name='zk-jobs', daemon=True).start()

    def submit(self, action: str, fn: Callable[[Job], None]) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], action=action)
        with self._lock:
            self._jobs[job.id] = job
            while len(self._jobs) > KEEP:
                self._jobs.popitem(last=False)
        self._queue.put((job, fn))
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def _work(self):
        while True:
            job, fn = self._queue.get()
            job.state = 'working'
            try:
                fn(job)
                job.state = 'done'
            except Exception as e:  # noqa: BLE001 - any failure belongs to the job, not the worker
                job.error = str(e) or type(e).__name__
                job.state = 'error'
            finally:
                job.step = ''
