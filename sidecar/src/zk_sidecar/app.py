"""The HTTP API. Every route needs the session token."""
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from pydantic import BaseModel

from . import __version__
from .config import Config
from .jobs import Jobs
from .kernels.env import VaultPython
from .kernels.pool import KernelError, KernelPool
from .sources.capture import capture, check_url
from .sources.convert import KINDS, kind_of
from .sources.service import SourceService
from .sources.store import SourceStore
from .summarise import Summariser, openai_complete

MAX_UPLOAD = 500 * 1024 * 1024


def default_service(config: Config) -> SourceService:
    def summariser() -> Summariser:
        complete = openai_complete(config.llm_base_url, config.llm_model, config.llm_api_key)
        return Summariser(complete, config.llm_context)

    def take(url: str, out: Path) -> None:
        capture(url, out, config.single_file, config.browser)

    return SourceService(SourceStore(config.vault), summariser, take)


class UrlIn(BaseModel):
    url: str


class RunIn(BaseModel):
    #: The note or notebook, relative to the vault. One kernel per path and kernel name.
    path: str
    code: str
    kernel: str = ''
    language: str = ''


class KernelIn(BaseModel):
    path: str
    kernel: str = ''


def create_app(config: Config, service: SourceService | None = None, pool: KernelPool | None = None) -> FastAPI:
    started = time.monotonic()
    sources = service or default_service(config)
    jobs = Jobs()
    kernels = pool or KernelPool(VaultPython(config.vault, config.python), config.kernel_idle_minutes)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        kernels.close()

    def auth(authorization: str = Header('')):
        expected = f'Bearer {config.token}'
        if not secrets.compare_digest(authorization.encode(), expected.encode()):
            raise HTTPException(401)

    app = FastAPI(title='zk-sidecar', version=__version__, dependencies=[Depends(auth)],
                  docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.kernels = kernels

    @app.get('/health')
    def health():
        return {'ok': True, 'version': __version__, 'uptime_s': round(time.monotonic() - started, 1)}

    @app.get('/config')
    def get_config():
        return {**config.public(), 'file_types': sorted(KINDS)}

    @app.get('/jobs/{job_id}')
    def get_job(job_id: str):
        if not (job := jobs.get(job_id)):
            raise HTTPException(404, 'no such job')
        return job.public()

    @app.get('/sources')
    def list_sources():
        return [{'slug': s.slug, 'title': s.meta.get('title', s.slug), 'kind': s.meta.get('kind'),
                 'note': sources.store.rel(s.note)} for s in sources.store.all()]

    @app.post('/sources/file', status_code=202)
    async def add_file(request: Request, name: str = Query(..., min_length=1)):
        name = Path(name).name
        if not kind_of(name):
            raise HTTPException(400, f'Unsupported file type. Use one of: {", ".join(sorted(KINDS))}')
        data = await request.body()
        if not data:
            raise HTTPException(400, 'The file is empty.')
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, 'The file is larger than 500 MB.')
        return jobs.submit('add-file', lambda job: sources.add_file(job, name, data)).public()

    @app.post('/sources/url', status_code=202)
    def add_url(body: UrlIn):
        try:
            url = check_url(body.url)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        return jobs.submit('add-url', lambda job: sources.add_url(job, url)).public()

    def existing(slug: str):
        try:
            return sources.store.get(slug)
        except KeyError:
            raise HTTPException(404, 'no such source') from None

    @app.post('/sources/{slug}/recapture', status_code=202)
    def recapture(slug: str):
        src = existing(slug)
        if src.meta.get('kind') != 'web':
            raise HTTPException(400, 'Only web pages can be captured again.')
        return jobs.submit('recapture', lambda job: sources.recapture(job, slug)).public()

    @app.post('/sources/{slug}/summarise', status_code=202)
    def summarise(slug: str):
        existing(slug)
        return jobs.submit('summarise', lambda job: sources.resummarise(job, slug)).public()

    @app.get('/kernels')
    def list_kernels():
        return {'kernels': kernels.kernels(), 'running': kernels.status(),
                'python': str(kernels.env.python), 'python_ready': not kernels.env.needs_setup()}

    @app.post('/runs', status_code=202)
    def start_run(body: RunIn):
        if not body.code.strip():
            raise HTTPException(400, 'There is no code to run.')
        try:
            return kernels.run(body.path, body.code, body.kernel, body.language).public()
        except KernelError as e:
            raise HTTPException(400, str(e)) from None

    @app.get('/runs/{run_id}')
    def get_run(run_id: str, since: int = -1, wait: float = Query(0, ge=0, le=10)):
        if not (run := kernels.get(run_id)):
            raise HTTPException(404, 'no such run')
        return run.wait(since, wait) if wait else run.public()

    def on_kernels(action):
        def route(body: KernelIn):
            try:
                return {'kernels': action(body.path, body.kernel)}
            except KernelError as e:
                raise HTTPException(400, str(e)) from None
        return route

    app.post('/kernels/interrupt')(on_kernels(kernels.interrupt))
    app.post('/kernels/restart')(on_kernels(kernels.restart))
    app.post('/kernels/shutdown')(on_kernels(kernels.shutdown))

    return app
