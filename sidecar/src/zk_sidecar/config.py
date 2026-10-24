"""Settings the plugin passes in through the environment.

Secrets go through the environment, not the command line, so they don't show up in `ps`.
"""
import glob
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path


def find_single_file() -> str:
    """SingleFile CLI: on PATH, or next to this Python (make setup installs it into the venv)."""
    local = Path(sys.executable).parent / 'single-file'
    return shutil.which('single-file') or (str(local) if local.exists() else '')


def find_browser() -> str:
    """A Chromium for SingleFile: the system one, or the newest Playwright download."""
    for name in ('chromium', 'chromium-browser', 'google-chrome-stable', 'google-chrome'):
        if path := shutil.which(name):
            return path
    found = sorted(glob.glob(os.path.expanduser('~/.cache/ms-playwright/chromium-*/chrome-linux*/chrome')))
    return found[-1] if found else ''


@dataclass(frozen=True)
class Config:
    vault: Path
    token: str
    llm_base_url: str = 'http://127.0.0.1:11434/v1'
    llm_model: str = 'gemma3:27b'
    llm_api_key: str = ''
    #: The model's context window in tokens. Long documents are split to fit it.
    llm_context: int = 8192
    embed_model: str = 'nomic-embed-text'
    single_file: str = ''
    browser: str = ''
    #: A Python with ipykernel for code. Empty means the vault's own, in .zk/python.
    python: str = ''
    #: Kernels with nothing to do shut down after this long.
    kernel_idle_minutes: float = 60
    #: When to rebuild the communities each day, HH:MM. Empty or 'off' turns it off.
    graph_schedule: str = '03:00'
    #: Language data for the phrase extractor, shared by every vault.
    data_dir: Path = Path('~/.cache/zk-sidecar').expanduser()

    @classmethod
    def from_env(cls, vault: Path, env: dict[str, str] | None = None) -> 'Config':
        env = os.environ if env is None else env
        token = env.get('ZK_TOKEN', '')
        if not token:
            raise ValueError('ZK_TOKEN is not set')
        defaults = cls(vault=vault, token=token)
        try:
            context = int(env.get('ZK_LLM_CONTEXT') or defaults.llm_context)
        except ValueError:
            raise ValueError('ZK_LLM_CONTEXT must be a whole number of tokens') from None
        schedule = env.get('ZK_GRAPH_SCHEDULE', defaults.graph_schedule)
        from .graph.schedule import parse
        parse(schedule)  # a bad time stops the sidecar here, with a message
        return cls(
            vault=vault,
            token=token,
            llm_base_url=env.get('ZK_LLM_BASE_URL') or defaults.llm_base_url,
            llm_model=env.get('ZK_LLM_MODEL') or defaults.llm_model,
            llm_api_key=env.get('ZK_LLM_API_KEY', ''),
            llm_context=max(context, 2048),
            embed_model=env.get('ZK_EMBED_MODEL') or defaults.embed_model,
            single_file=env.get('ZK_SINGLE_FILE') or find_single_file(),
            browser=env.get('ZK_BROWSER') or find_browser(),
            python=env.get('ZK_PYTHON', ''),
            graph_schedule=schedule,
        )

    def public(self) -> dict:
        """Settings safe to send back to the plugin. The key is never sent."""
        return {
            'vault': str(self.vault),
            'llm_base_url': self.llm_base_url,
            'llm_model': self.llm_model,
            'llm_context': self.llm_context,
            'has_api_key': bool(self.llm_api_key),
            'embed_model': self.embed_model,
            'single_file': self.single_file,
            'browser': self.browser,
            'python': self.python,
            'graph_schedule': self.graph_schedule,
        }
