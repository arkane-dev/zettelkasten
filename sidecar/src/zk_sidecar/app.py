"""The HTTP API. Every route needs the session token."""
import secrets
import time

from fastapi import Depends, FastAPI, Header, HTTPException

from . import __version__
from .config import Config


def create_app(config: Config) -> FastAPI:
    started = time.monotonic()

    def auth(authorization: str = Header('')):
        expected = f'Bearer {config.token}'
        if not secrets.compare_digest(authorization.encode(), expected.encode()):
            raise HTTPException(401)

    app = FastAPI(title='zk-sidecar', version=__version__, dependencies=[Depends(auth)],
                  docs_url=None, redoc_url=None, openapi_url=None)

    @app.get('/health')
    def health():
        return {'ok': True, 'version': __version__, 'uptime_s': round(time.monotonic() - started, 1)}

    @app.get('/config')
    def get_config():
        return config.public()

    return app
